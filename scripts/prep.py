"""Prépare les données de la storymap à partir du projet QGIS atlas_departemental."""
import json, re, unicodedata, sys
from pathlib import Path
import geopandas as gpd, pandas as pd, numpy as np, warnings
warnings.filterwarnings("ignore")
SRC = Path(sys.argv[1]); OUT = Path(sys.argv[2])
(OUT / "data" / "v12" / "dept").mkdir(parents=True, exist_ok=True)

def key(s):
    s = unicodedata.normalize("NFKD", str(s)).encode("ascii", "ignore").decode().lower()
    s = re.sub(r"[^a-z0-9]", "", s)
    return {"mafouetafamba": "mefouetafamba", "malentouen": "malantouen"}.get(s, s)

def slug(s):
    s = unicodedata.normalize("NFKD", str(s)).encode("ascii", "ignore").decode().lower()
    return re.sub(r"[^a-z0-9]+", "-", s).strip("-")

REG_FR = {"1": "Centre", "2": "Est", "3": "Extrême-Nord", "4": "Nord", "5": "Nord-Ouest", "6": "Ouest",
          "7": "Sud-Ouest", "8": "Adamaoua", "9": "Littoral", "10": "Sud"}
dl = pd.read_excel(SRC / "departement_list.xlsx")
dl["k"] = dl.Nom_Dept.map(key)
com = gpd.read_file(SRC / "commune_arrondissement.shp").to_crs(4326)
com["k"] = com.Nom_Dept.map(key)
assert set(com.k) == set(dl.k), set(com.k) ^ set(dl.k)
# --- départements = union des communes (le shapefile département du projet ne contient que le Sud-Ouest)
dep = com.dissolve("k").reset_index()[["k", "geometry"]]
dep["geometry"] = dep.geometry.buffer(0)
dep = dep.merge(dl.rename(columns={"%pop_couvrte": "pop1h"})[["k", "Nom_Dept", "Code_Dept", "Code_Reg", "pop1h", "population couverte", "population"]], on="k")
REG_FR = {"adamaoua": "Adamaoua", "centre": "Centre", "est": "Est", "extremenord": "Extrême-Nord", "littoral": "Littoral", "nord": "Nord", "northwest": "Nord-Ouest", "nordouest": "Nord-Ouest", "ouest": "Ouest", "sud": "Sud", "southwest": "Sud-Ouest", "sudouest": "Sud-Ouest"}
dep["region"] = dep.k.map(dict(zip(com.k, com.Nom_Region.map(key)))).map(REG_FR)
assert dep.region.notna().all()
dep["slug"] = dep.Nom_Dept.map(slug)

ds = gpd.read_file(SRC / "District_S.shp").to_crs(4326)
dx = pd.read_excel(SRC / "districts.xlsx")
dx["k"] = dx.Nom_Dept.map(key)
dx["kd"] = dx.District_S.map(key)
cb = pd.read_excel(SRC / "commune_bis.xlsx")
cb["k"] = cb.Nom_Dept.map(key)
cb["kc"] = cb.Nom_commun.map(key)
UNMATCHED = {"districts": [], "communes": []}
fo = gpd.read_file(SRC / "fosa_fonc_privee.shp", columns=["Fosa", "categorie", "statut_fos", "District d", "Aire de sa"]).to_crs(4326)
fo = fo[fo.geometry.notna()]
# la couche contient des doublons (même identifiant, même position) : on ne garde qu'une ligne par FOSA
fo_ids = gpd.read_file(SRC / "fosa_fonc_privee.shp", columns=["id"]).loc[fo.index, "id"]
n_avant = len(fo)
fo = fo[~fo_ids.duplicated()]
print(f"FOSA : {n_avant} lignes, {n_avant - len(fo)} doublons supprimés -> {len(fo)}")

# --- règle : un district de santé appartient à UN seul département, celui qui contient la plus grande part de sa surface
M = "ESRI:102022"
ds["geometry"] = ds.geometry.buffer(0)
_ov = gpd.overlay(ds[["District_S", "geometry"]].to_crs(M), dep[["k", "geometry"]].to_crs(M), how="intersection", keep_geom_type=True)
_ov["a"] = _ov.area
_ov["part"] = _ov.a / _ov.District_S.map(_ov.groupby("District_S").a.sum()) * 100
ds["dept_k"] = ds.District_S.map(_ov.sort_values("a").groupby("District_S").k.last())
DEP_NAME = dict(zip(dep.k, dep.Nom_Dept))
# exceptions décidées par le MINSANTE (district -> département), prioritaires sur la règle de la surface
EXCEPTIONS = {"Nkolafamba": "Mefou et Afamba"}
for n, dn in EXCEPTIONS.items():
    ds.loc[ds.District_S == n, "dept_k"] = key(dn)
_mult = _ov[_ov.part >= 1].groupby("District_S").filter(lambda g: len(g) > 1)
A_CHEVAL = {}
for dn, g in _mult.groupby("District_S"):
    lst = [(DEP_NAME[r.k], round(float(r.part))) for r in g.sort_values("part", ascending=False).itertuples()]
    if dn in EXCEPTIONS:   # le département de rattachement en premier
        lst.sort(key=lambda x: x[0] != EXCEPTIONS[dn])
    A_CHEVAL[dn] = lst
xl_dept = dict(zip(dx.kd, dx.k))
print("Districts à cheval :", {k: v for k, v in A_CHEVAL.items()})
print("Rattachement modifié :", [(n, DEP_NAME.get(xl_dept.get(key(n)), "?"), DEP_NAME[k]) for n, k in zip(ds.District_S, ds.dept_k) if xl_dept.get(key(n)) != k])
CAT_PUB = {"hr_hra": ["hr_hra"], "hd": ["hd"], "cma": ["cma"], "csi": ["csi", "infirmerie"]}
# --- district de chaque FOSA : district DÉCLARÉ (champ « District d », noms harmonisés), sinon district où tombe le point
ALIAS_DS = {"kumba": "kumbasouth", "makary": "makari", "niete": "nyeete", "eyumodjock": "eyumojock", "malentouen": "malantouen"}
GEO_DS = {key(n): n for n in ds.District_S}
XL_ONLY = {r.kd: r.District_S for r in dx.itertuples() if r.kd not in GEO_DS}   # districts sans contour (Makak, Mokong)
def declared(v):
    if v is None or (isinstance(v, float) and np.isnan(v)):
        return None
    s = re.sub(r"^\s*District\s+(de\s+sant[ée]\s+)?", "", str(v), flags=re.I).strip()
    kk = ALIAS_DS.get(key(s), key(s))
    return GEO_DS.get(kk) or XL_ONLY.get(kk)
_pos = gpd.sjoin(fo[["geometry"]], ds[["District_S", "geometry"]], how="left", predicate="within")
fo["dpos"] = _pos[~_pos.index.duplicated()]["District_S"]
fo["ddec"] = fo["District d"].map(declared)
fo["dfin"] = fo.ddec.where(fo.ddec.notna(), fo.dpos)
fo["pv"] = ((fo.dfin != fo.dpos) & fo.dfin.notna()).astype(int)    # position à vérifier
fo["dept_ds"] = fo.dfin.map({**dict(zip(ds.District_S, ds.dept_k)), **{v: dx.loc[dx.kd == k, "k"].iloc[0] for k, v in XL_ONLY.items()}})
# --- HARMONISATION : chaque FOSA a UN département (celui de son district), UN district et UNE commune de ce département
_dp = gpd.sjoin(fo[["geometry"]], dep[["k", "geometry"]], how="left", predicate="within")
fo["dept_f"] = fo.dept_ds.where(fo.dept_ds.notna(), _dp[~_dp.index.duplicated()]["k"])
_cp = gpd.sjoin(fo[["geometry"]], com[["Nom_commun", "k", "geometry"]], how="left", predicate="within")
_cp = _cp[~_cp.index.duplicated()]
fo["cm_f"] = _cp["Nom_commun"].where(_cp["k"] == fo.dept_f)
_need = fo.cm_f.isna() & fo.dept_f.notna()
if _need.any():   # point hors de son département : commune la plus proche dans le département de son district
    fo_m, com_m_all = fo.to_crs(M), com.to_crs(M)
    for i in fo.index[_need]:
        cands = com_m_all[com_m_all.k == fo.at[i, "dept_f"]]
        fo.at[i, "cm_f"] = cands.Nom_commun.iloc[int(cands.distance(fo_m.at[i, "geometry"]).values.argmin())]
print(f"Harmonisation : {int(_need.sum())} FOSA rattachées à la commune la plus proche de leur département ; sans département : {int(fo.dept_f.isna().sum())}")
print(f"FOSA : {int(fo.ddec.notna().sum())} rattachées par leur district déclaré, {int(fo.ddec.isna().sum())} par leur position ; {int(fo.pv.sum())} positions à vérifier")

def fosa_counts(sel):
    pub = sel[sel.statut_fos.isin(["public", "parapublic"])]
    out = {"nb_fosa": int(len(sel)), "nb_pub": int(len(pub)), "nb_priv": int(len(sel) - len(pub))}
    for c, cats in CAT_PUB.items():
        out[c] = int(pub.categorie.isin(cats).sum())
    return out

# --- indicateurs (mêmes expressions que la mise en page : within(point_on_surface, @atlas_geometry))
def count_within(gdf, poly, pos=True):
    pts = gdf.geometry.representative_point() if pos else gdf.geometry
    return pts.within(poly)

def num(v):
    try:
        f = float(v)
        return None if np.isnan(f) else (int(f) if f.is_integer() else round(f, 1))
    except Exception:
        return None if pd.isna(v) else str(v)

routes = gpd.read_file(SRC / "routes.shp", columns=["label_n"], where="label_n IN ('main road','secondary')").to_crs(4326)
rivers = gpd.read_file(SRC / "cours_eaux.shp", columns=["waterway", "name"]).to_crs(4326)
rivers = rivers[rivers.waterway.isin(["river", "stream", "canal", "wadi"])]

ds_pos = ds.geometry.representative_point(); com_pos = com.geometry.representative_point()
summary = []
FOSA_COLS = ["District_S", "Pop_2026", "public", "hr_hra", "hd", "cma", "csi", "classification", "% Population à 1h FOSA", "communes_couvertes"]
COM_COLS = ["Nom_commun", "public", "prive", "hr_hra", "hd", "cma", "csi"]
def fc(gdf, props, prec=5):
    gdf = gdf.copy()
    return json.loads(gdf[props + ["geometry"]].to_json(drop_id=True, to_wgs84=False))

def round_coords(obj, p=5):
    if isinstance(obj, list):
        return [round_coords(x, p) for x in obj]
    if isinstance(obj, float):
        return round(obj, p)
    if isinstance(obj, dict):
        return {k: round_coords(v, p) for k, v in obj.items()}
    return obj

def commune_rows(in_com, in_fo):
    rows = []
    for n in sorted(in_com.Nom_commun, key=key):
        c = fosa_counts(in_fo[in_fo.cm_f == n])
        rows.append([str(n), c["nb_pub"], c["nb_priv"], c["hr_hra"], c["hd"], c["cma"], c["csi"]])
    return rows

def district_rows(tab_ds, in_ds_geo, poly):
    """Une ligne par district : FOSA comptées sur les points (doublons retirés), autres colonnes de districts.xlsx."""
    rows = []
    geo = {key(n): g for n, g in zip(in_ds_geo.District_S, in_ds_geo.geometry)}
    geo_name = {key(n): n for n in in_ds_geo.District_S}
    for _, r in tab_ds.iterrows():
        g = geo.get(r.kd)
        txt = lambda c: None if pd.isna(r[c]) or str(r[c]).strip() in ("", "/") else str(r[c]).strip().rstrip(",")
        if g is not None:
            sel = fo[fo.dfin == geo_name[r.kd]]
            cnt = fosa_counts(sel)
            hors = 0
            pop = num(ds.loc[ds.District_S == geo_name[r.kd], "Pop_2026"].iloc[0])
            name = geo_name[r.kd]
        else:   # district absent de la couche cartographique : FOSA qui le déclarent
            name = XL_ONLY.get(r.kd, str(r.District_S))
            cnt = fosa_counts(fo[fo.dfin == name])
            hors, pop = 0, num(r["Pop_2026"])
        parts = A_CHEVAL.get(name, [])
        rows.append([name, pop, cnt["nb_pub"], cnt["nb_priv"], cnt["hr_hra"], cnt["hd"], cnt["cma"], cnt["csi"],
                     txt("classification"), num(r["% Population à 1h FOSA"]), txt("communes_couvertes"), hors,
                     "; ".join(f"{a} {b} %" for a, b in parts)])
    return sorted(rows, key=lambda x: key(x[0]))

for d in dep.itertuples():
    poly = d.geometry
    in_fo = fo[fo.dept_f == d.k]
    in_com = com[com.k == d.k]
    # districts du département = ceux dont la plus grande part est dans le département (+ ceux absents de la carte, d'après districts.xlsx)
    in_ds_geo = ds[ds.dept_k == d.k]
    geo_keys = set(ds.District_S.map(key))
    tab_ds = dx[dx.kd.isin(set(in_ds_geo.District_S.map(key))) | ((dx.k == d.k) & ~dx.kd.isin(geo_keys))]
    # FOSA des districts du département situées hors du département (affichées seulement quand on isole le district)
    union_ds = in_ds_geo.geometry.union_all() if len(in_ds_geo) else None
    fo_hors = fo.iloc[0:0]
    tab_cb = cb[cb.k == d.k].sort_values("Nom_commun")
    stats = {
        "nom": d.Nom_Dept, "slug": d.slug, "region": d.region, "code": d.Code_Dept,
        "fosa": int(len(in_fo)), "communes": int(len(in_com)), "districts": int(len(tab_ds)),
        "population": int(pd.to_numeric(in_ds_geo.Pop_2026, errors="coerce").sum()), "pop1h": num(d.pop1h),
        "fosa_publiques": int(in_fo.statut_fos.isin(["public", "parapublic"]).sum()),
        "fosa_privees": int(len(in_fo) - in_fo.statut_fos.isin(["public", "parapublic"]).sum()),
        "bbox": [round(v, 4) for v in poly.bounds],
        "tab_districts": district_rows(tab_ds, in_ds_geo, poly),
        "tab_communes": commune_rows(in_com, in_fo),
        "districts_voisins": [],
        "fosa_sans_district": int(in_fo.dfin.isna().sum()),
    }
    summary.append(stats)
    buf = poly.buffer(0.02)
    r_clip = gpd.clip(routes[routes.intersects(buf)], buf)
    w_clip = gpd.clip(rivers[rivers.intersects(buf)], buf)
    tol = 0.0004
    # --- relations district <-> commune (recouvrement > 5 % de la plus petite des deux surfaces)
    ds_m, com_m = in_ds_geo.to_crs("ESRI:102022"), in_com.to_crs("ESRI:102022")
    d2c = {n: [] for n in in_ds_geo.District_S}
    c2d = {n: [] for n in in_com.Nom_commun}
    for dn, dg in zip(in_ds_geo.District_S, ds_m.geometry):
        for cn, cg in zip(in_com.Nom_commun, com_m.geometry):
            if not dg.intersects(cg):
                continue
            a = dg.intersection(cg).area
            if a > 0.05 * min(dg.area, cg.area):
                d2c[dn].append(cn)
                c2d[cn].append(dn)
    # FOSA : district et commune d'appartenance (spatial)
    in_fo = in_fo.copy()
    fo_lay_hors = fo_hors.copy()
    fo_lay_hors["ds"] = fo_lay_hors.dfin
    fo_lay_hors["cm"] = None
    in_fo["ds"] = in_fo.dfin
    in_fo["cm"] = in_fo.cm_f
    # --- attributs cliquables : district de santé
    dprops = []
    for _, g in in_ds_geo.iterrows():
        sel = in_fo[in_fo.within(g.geometry)]
        row = dx[(dx.kd == key(g.District_S)) & (dx.k == d.k)]
        if row.empty:
            row = dx[dx.kd == key(g.District_S)]
        if row.empty:
            UNMATCHED["districts"].append(g.District_S)
        r = row.iloc[0] if len(row) else {}
        dprops.append({
            "communes_in": "|".join(sorted(d2c.get(g.District_S, []))),
            "pop": num(g.Pop_2026), "classification": None if not len(row) or pd.isna(r["classification"]) else str(r["classification"]),
            "pop1h": num(r["% Population à 1h FOSA"]) if len(row) else None,
            "communes_couvertes": None if not len(row) or pd.isna(r["communes_couvertes"]) else str(r["communes_couvertes"]).strip().rstrip(","),
            **fosa_counts(fo[fo.dfin == g.District_S]),
        })
    ds_layer = in_ds_geo.assign(geometry=in_ds_geo.geometry.simplify(tol)).reset_index(drop=True)
    ds_layer = pd.concat([ds_layer[["District_S", "geometry"]], pd.DataFrame(dprops)], axis=1)
    # --- attributs cliquables : commune
    cprops = []
    for _, g in in_com.iterrows():
        sel = in_fo[in_fo.cm_f == g.Nom_commun]
        row = cb[(cb.kc == key(g.Nom_commun)) & (cb.k == d.k)]
        if row.empty:
            UNMATCHED["communes"].append(g.Nom_commun)
        r = row.iloc[0] if len(row) else {}
        cprops.append({
            "districts_in": "|".join(sorted(c2d.get(g.Nom_commun, []))),
            "departement": d.Nom_Dept,
            **{("public" if k2 == "nb_pub" else "prive" if k2 == "nb_priv" else k2): v for k2, v in fosa_counts(sel).items()},
        })
    com_layer = in_com.assign(geometry=in_com.geometry.simplify(tol)).reset_index(drop=True)
    com_layer = pd.concat([com_layer[["Nom_commun", "geometry"]], pd.DataFrame(cprops)], axis=1)
    layers = {
        "communes": fc(gpd.GeoDataFrame(com_layer, geometry="geometry", crs=4326), [c for c in com_layer.columns if c != "geometry"]),
        "districts": fc(gpd.GeoDataFrame(ds_layer, geometry="geometry", crs=4326), [c for c in ds_layer.columns if c != "geometry"]),
        "fosa": fc(pd.concat([in_fo.assign(hors=0), fo_lay_hors.assign(hors=1)]).rename(columns={"District d": "district", "Aire de sa": "aire"}), ["Fosa", "categorie", "statut_fos", "district", "aire", "ds", "cm", "hors", "pv", "dpos"]),
        "routes": fc(r_clip.assign(geometry=r_clip.geometry.simplify(tol / 2)).explode(index_parts=False), ["label_n"]),
        "labels": [[str(n), "c", round(pt.x, 4), round(pt.y, 4)] for n, pt in zip(in_com.Nom_commun, in_com.geometry.representative_point())]
                  + [[str(n), "d", round(pt.x, 4), round(pt.y, 4)] for n, pt in zip(in_ds_geo.District_S, in_ds_geo.geometry.representative_point())],
        "rivieres": fc(w_clip.assign(geometry=w_clip.geometry.simplify(tol / 2)).explode(index_parts=False), ["waterway", "name"]),
    }
    (OUT / "data" / "v12" / "dept" / f"{d.slug}.json").write_text(json.dumps(round_coords(layers), ensure_ascii=False, separators=(",", ":")))

# national
depg = dep.assign(geometry=dep.geometry.simplify(0.003))
nat = fc(depg, ["Nom_Dept", "slug", "region"])
for f, pt in zip(nat["features"], dep.geometry.representative_point()):
    f["properties"]["lbl"] = [round(pt.x, 4), round(pt.y, 4)]
pays = gpd.GeoDataFrame(geometry=[dep.union_all().simplify(0.003)], crs=4326)
(OUT / "data" / "v12" / "departements.json").write_text(json.dumps(round_coords(nat, 4), ensure_ascii=False, separators=(",", ":")))
(OUT / "data" / "v12" / "cameroun.json").write_text(json.dumps(round_coords(fc(pays, []), 4), separators=(",", ":")))
order = ["Adamaoua", "Centre", "Est", "Extrême-Nord", "Littoral", "Nord", "Nord-Ouest", "Ouest", "Sud", "Sud-Ouest"]
summary.sort(key=lambda s: (order.index(s["region"]), key(s["nom"])))
(OUT / "data" / "v12" / "atlas.json").write_text(json.dumps({"regions": order, "departements": summary}, ensure_ascii=False, separators=(",", ":")))
print("Sans correspondance :", UNMATCHED)
pd.DataFrame([{"district": n, "repartition": "; ".join(f"{a} {b} %" for a, b in v), "rattache_a": v[0][0]} for n, v in sorted(A_CHEVAL.items())]).to_csv(OUT / "data" / "v12" / "districts_a_cheval.csv", index=False)
tot = pd.DataFrame(summary)
print(tot.groupby("region")[["fosa", "communes", "districts", "population"]].sum())
print("TOTAL", tot[["fosa", "communes", "districts", "population"]].sum().to_dict(), "FOSA hors départements:", len(fo) - tot.fosa.sum())

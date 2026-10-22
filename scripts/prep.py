"""Prépare les données de la storymap à partir du projet QGIS atlas_departemental."""
import json, re, unicodedata, sys
from pathlib import Path
import geopandas as gpd, pandas as pd, numpy as np, warnings
warnings.filterwarnings("ignore")
SRC = Path(sys.argv[1]); OUT = Path(sys.argv[2])
(OUT / "data" / "dept").mkdir(parents=True, exist_ok=True)

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

for d in dep.itertuples():
    poly = d.geometry
    in_fo = fo[fo.within(poly)]
    in_com = com[com_pos.within(poly)]
    in_ds_geo = ds[ds_pos.within(poly)]
    tab_ds = dx[dx.k == d.k]
    tab_cb = cb[cb.k == d.k].sort_values("Nom_commun")
    stats = {
        "nom": d.Nom_Dept, "slug": d.slug, "region": d.region, "code": d.Code_Dept,
        "fosa": int(len(in_fo)), "communes": int(len(in_com)), "districts": int(len(tab_ds)),
        "population": int(in_ds_geo.Pop_2026.sum()), "pop1h": num(d.pop1h),
        "fosa_publiques": int(in_fo.statut_fos.isin(["public", "parapublic"]).sum()),
        "fosa_privees": int(in_fo.statut_fos.isin(["prive_laic", "prive_confessionnel"]).sum()),
        "bbox": [round(v, 4) for v in poly.bounds],
        "tab_districts": [[num(r[c]) if c not in ("District_S", "classification", "communes_couvertes") else (None if pd.isna(r[c]) else str(r[c]).strip().rstrip(",")) for c in FOSA_COLS] for _, r in tab_ds.sort_values("District_S").iterrows()],
        "tab_communes": [[num(r[c]) if c != "Nom_commun" else str(r[c]) for c in COM_COLS] for _, r in tab_cb.iterrows()],
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
    in_fo["ds"] = gpd.sjoin(in_fo[["geometry"]], in_ds_geo[["District_S", "geometry"]], how="left", predicate="within").groupby(level=0)["District_S"].first()
    in_fo["cm"] = gpd.sjoin(in_fo[["geometry"]], in_com[["Nom_commun", "geometry"]], how="left", predicate="within").groupby(level=0)["Nom_commun"].first()
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
            "nb_fosa": int(len(sel)), "nb_pub": int(sel.statut_fos.isin(["public", "parapublic"]).sum()),
            "nb_priv": int(sel.statut_fos.isin(["prive_laic", "prive_confessionnel"]).sum()),
            **{c: (num(r[c]) if len(row) else None) for c in ("hr_hra", "hd", "cma", "csi")},
        })
    ds_layer = in_ds_geo.assign(geometry=in_ds_geo.geometry.simplify(tol)).reset_index(drop=True)
    ds_layer = pd.concat([ds_layer[["District_S", "geometry"]], pd.DataFrame(dprops)], axis=1)
    # --- attributs cliquables : commune
    cprops = []
    for _, g in in_com.iterrows():
        sel = in_fo[in_fo.within(g.geometry)]
        row = cb[(cb.kc == key(g.Nom_commun)) & (cb.k == d.k)]
        if row.empty:
            UNMATCHED["communes"].append(g.Nom_commun)
        r = row.iloc[0] if len(row) else {}
        cprops.append({
            "districts_in": "|".join(sorted(c2d.get(g.Nom_commun, []))),
            "departement": d.Nom_Dept, "nb_fosa": int(len(sel)),
            **{c: (num(r[c]) if len(row) else None) for c in ("public", "prive", "hr_hra", "hd", "cma", "csi")},
        })
    com_layer = in_com.assign(geometry=in_com.geometry.simplify(tol)).reset_index(drop=True)
    com_layer = pd.concat([com_layer[["Nom_commun", "geometry"]], pd.DataFrame(cprops)], axis=1)
    layers = {
        "communes": fc(gpd.GeoDataFrame(com_layer, geometry="geometry", crs=4326), [c for c in com_layer.columns if c != "geometry"]),
        "districts": fc(gpd.GeoDataFrame(ds_layer, geometry="geometry", crs=4326), [c for c in ds_layer.columns if c != "geometry"]),
        "fosa": fc(in_fo.rename(columns={"District d": "district", "Aire de sa": "aire"}), ["Fosa", "categorie", "statut_fos", "district", "aire", "ds", "cm"]),
        "routes": fc(r_clip.assign(geometry=r_clip.geometry.simplify(tol / 2)).explode(index_parts=False), ["label_n"]),
        "labels": [[str(n), "c", round(pt.x, 4), round(pt.y, 4)] for n, pt in zip(in_com.Nom_commun, in_com.geometry.representative_point())]
                  + [[str(n), "d", round(pt.x, 4), round(pt.y, 4)] for n, pt in zip(in_ds_geo.District_S, in_ds_geo.geometry.representative_point())],
        "rivieres": fc(w_clip.assign(geometry=w_clip.geometry.simplify(tol / 2)).explode(index_parts=False), ["waterway", "name"]),
    }
    (OUT / "data" / "dept" / f"{d.slug}.json").write_text(json.dumps(round_coords(layers), ensure_ascii=False, separators=(",", ":")))

# national
depg = dep.assign(geometry=dep.geometry.simplify(0.003))
nat = fc(depg, ["Nom_Dept", "slug", "region"])
for f, pt in zip(nat["features"], dep.geometry.representative_point()):
    f["properties"]["lbl"] = [round(pt.x, 4), round(pt.y, 4)]
pays = gpd.GeoDataFrame(geometry=[dep.union_all().simplify(0.003)], crs=4326)
(OUT / "data" / "departements.json").write_text(json.dumps(round_coords(nat, 4), ensure_ascii=False, separators=(",", ":")))
(OUT / "data" / "cameroun.json").write_text(json.dumps(round_coords(fc(pays, []), 4), separators=(",", ":")))
order = ["Adamaoua", "Centre", "Est", "Extrême-Nord", "Littoral", "Nord", "Nord-Ouest", "Ouest", "Sud", "Sud-Ouest"]
summary.sort(key=lambda s: (order.index(s["region"]), key(s["nom"])))
(OUT / "data" / "atlas.json").write_text(json.dumps({"regions": order, "departements": summary}, ensure_ascii=False, separators=(",", ":")))
print("Sans correspondance :", UNMATCHED)
tot = pd.DataFrame(summary)
print(tot.groupby("region")[["fosa", "communes", "districts", "population"]].sum())
print("TOTAL", tot[["fosa", "communes", "districts", "population"]].sum().to_dict(), "FOSA hors départements:", len(fo) - tot.fosa.sum())

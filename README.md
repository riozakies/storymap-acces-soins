# Atlas sanitaire départemental – Cameroun (storymap)

Storymap web de l'atlas sanitaire départemental (projet QGIS `atlas_departemental.qgz`), publiée avec GitHub Pages.

- **Vue nationale** : part de la population à moins d'une heure d'une FOSA, par département. Un clic sur un département ouvre son atlas.
- **Filtre « Région »** : chaque région ouvre un chapitre de synthèse, puis l'atlas de chacun de ses départements (ordre alphabétique, comme l'atlas QGIS).
- **Chapitre départemental** : total FOSA, population estimée 2026, communes, districts de santé, % de population à 1 h, tableaux « FOSA par district de santé » et « FOSA par commune », carte (communes, districts, FOSA publiques par catégorie, FOSA privées, routes principales et secondaires, cours d'eau), encart de situation.
- **Liens directs** : `…/#region=Littoral&dept=wouri`.

## Contenu

| Dossier | Rôle |
|---|---|
| `index.html`, `css/`, `js/app.js` | la page (MapLibre GL, aucun serveur nécessaire) |
| `data/atlas.json` | indicateurs et tableaux des 58 départements |
| `data/departements.json`, `data/cameroun.json` | limites nationales simplifiées |
| `data/dept/<departement>.json` | couches de chaque département, chargées à la demande |
| `vendor/` | MapLibre GL 4.7.1 (BSD-3) et police Source Sans 3 (OFL) |
| `scripts/prep.py` | régénère `data/` à partir du dossier `data/` du projet QGIS |

## Mettre à jour les données

```bash
pip install geopandas openpyxl
python scripts/prep.py chemin/vers/atlas_departemental/data .
```

puis déposer les fichiers modifiés de `data/` dans le dépôt.

## Notes

- Indicateurs calculés comme dans la mise en page QGIS : FOSA, communes et population (districts) comptés par `within(point_on_surface(...), @atlas_geometry)` ; districts comptés dans `districts.xlsx` ; % population à 1 h depuis `departement_list.xlsx`.
- Les contours des départements sont reconstitués par fusion des communes (le `departement.shp` fourni ne contient que le Sud-Ouest).
- Le raster de population WorldPop n'était pas dans le dossier du projet : il n'est pas affiché.
- Fond de carte : Esri World Topographic (comme dans le projet QGIS).

Sources : Direction de l'Organisation des Soins et de la Technologie Sanitaires,Cellule des Informations Sanitaires (CIS).

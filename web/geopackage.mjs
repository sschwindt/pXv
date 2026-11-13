import { DatabaseSync } from 'node:sqlite';

const envelopeBytes = [0, 32, 48, 48, 64];
const pointBytes = new Map([[1, 21], [1001, 29], [2001, 29], [3001, 37]]);
const quoteIdentifier = name => `"${name.replaceAll('"', '""')}"`;

function decodePoint(blob, srsId) {
  if (blob == null) return { x: null, y: null };
  if (!(blob instanceof Uint8Array) || blob.length < 8 ||
      blob[0] !== 0x47 || blob[1] !== 0x50 || blob[2] !== 0) {
    throw new Error('Invalid GeoPackage geometry header.');
  }
  const flags = blob[3];
  const envelope = envelopeBytes[(flags >> 1) & 7];
  const view = new DataView(blob.buffer, blob.byteOffset, blob.byteLength);
  if (envelope === undefined || (flags & 0xe0) || view.getInt32(4, Boolean(flags & 1)) !== srsId) {
    throw new Error('Unsupported GeoPackage geometry flags or spatial reference.');
  }
  if (flags & 0x10) return { x: null, y: null };

  const wkb = 8 + envelope;
  if (blob.length < wkb + 21 || (blob[wkb] !== 0 && blob[wkb] !== 1)) {
    throw new Error('Invalid WKB point.');
  }
  const littleEndian = blob[wkb] === 1;
  const size = pointBytes.get(view.getUint32(wkb + 1, littleEndian));
  if (!size || blob.length !== wkb + size) {
    throw new Error('Expected a Point geometry in the velocity layer.');
  }
  const x = view.getFloat64(wkb + 5, littleEndian);
  const y = view.getFloat64(wkb + 13, littleEndian);
  return Number.isFinite(x) && Number.isFinite(y) ? { x, y } : { x: null, y: null };
}

export function readPointLayers(path, selectedLayer) {
  const db = new DatabaseSync(path, { readOnly: true, allowExtension: false });
  try {
    db.exec('PRAGMA trusted_schema = OFF');
    const metadata = db.prepare(`
      SELECT geometry.table_name AS name, geometry.column_name AS columnName, geometry.srs_id AS srsId,
             CASE WHEN upper(srs.organization) = 'EPSG' THEN srs.organization_coordsys_id END AS epsg
      FROM gpkg_geometry_columns AS geometry
      JOIN gpkg_contents AS contents USING (table_name)
      JOIN gpkg_spatial_ref_sys AS srs ON geometry.srs_id = srs.srs_id
      JOIN sqlite_schema AS schema ON schema.name = geometry.table_name AND schema.type = 'table'
      WHERE contents.data_type = 'features' AND upper(geometry.geometry_type_name) = 'POINT'
      ORDER BY geometry.table_name
    `).all();
    const layers = metadata.map(({ name, epsg }) => ({ name, epsg: epsg > 0 ? epsg : null }));
    if (!layers.length) throw new Error('The GeoPackage has no point layers.');
    const selected = selectedLayer ? metadata.find(layer => layer.name === selectedLayer) : metadata[0];
    if (!selected) throw new Error('Choose a point layer from this GeoPackage.');
    const features = db.prepare(`SELECT * FROM ${quoteIdentifier(selected.name)} LIMIT 50001`).all();
    if (features.length > 50000) throw new Error('Select a survey layer with at most 50,000 points.');
    const points = features.map((row, index) => {
      const { [selected.columnName]: geometry, ...attributes } = row;
      const { x, y } = decodePoint(geometry, selected.srsId);
      const properties = Object.fromEntries(Object.entries(attributes)
        .filter(([, value]) => !(value instanceof Uint8Array)));
      return { type: 'Feature', id: properties.fid ?? index + 1, properties,
        geometry: x === null ? null : { type: 'Point', coordinates: [x, y] } };
    });
    return { layers, layer: selected.name, epsg: selected.epsg > 0 ? selected.epsg : null,
      data: { type: 'FeatureCollection', features: points } };
  } finally {
    db.close();
  }
}

/** Read a FlowTracker survey using the Isar field names and projected coordinate system. */
export function readSurvey(path, { layer = 'ft_tke_points' } = {}) {
  const db = new DatabaseSync(path, { readOnly: true, allowExtension: false });
  try {
    db.exec('PRAGMA trusted_schema = OFF');
    const metadata = db.prepare(`
      SELECT geometry.column_name AS geometryColumn, geometry.geometry_type_name AS geometryType,
             geometry.srs_id AS srsId, srs.organization, srs.organization_coordsys_id AS epsg
      FROM gpkg_geometry_columns AS geometry
      JOIN gpkg_contents AS contents USING (table_name)
      JOIN gpkg_spatial_ref_sys AS srs ON geometry.srs_id = srs.srs_id
      WHERE geometry.table_name = ? AND contents.data_type = 'features'
    `).get(layer);
    if (!metadata || metadata.geometryType.toUpperCase() !== 'POINT') {
      throw new Error(`GeoPackage requires a Point feature layer named "${layer}".`);
    }
    if (metadata.organization.toUpperCase() !== 'EPSG' || metadata.epsg !== 25832) {
      throw new Error('Survey points must use ETRS89 / UTM zone 32N (EPSG:25832).');
    }

    const rows = db.prepare(`
      SELECT fid AS id, Point_name AS name, ${quoteIdentifier(metadata.geometryColumn)} AS geometry,
             v_x_m_s AS u, v_y_m_s AS v, Meas_Depth_m AS measurementDepth, Total_Depth_m AS totalDepth
      FROM ${quoteIdentifier(layer)} ORDER BY fid
    `).all().map(({ geometry, ...row }) => ({ ...row, ...decodePoint(geometry, metadata.srsId) }));

    return { epsg: metadata.epsg, layer, rows };
  } finally {
    db.close();
  }
}

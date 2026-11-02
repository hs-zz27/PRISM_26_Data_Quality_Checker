/** Synthetic geometry for editing tests, with two independent source identities. */
import type { JsonObject } from './jsonGeometry';

export function polygonObjects(): JsonObject[] {
  return [
    { coordinate_system: 'zero_based_continuous', object_id: 1, source_key: '/shapes/0', source_id: null,
      group_id: 42, label: 'person', bbox_origin: 'derived_from_polygon', bbox: [10.25, 10, 40, 60],
      polygons: [[[10.25, 10], [40, 10], [40, 60], [10.25, 60]]] },
    { coordinate_system: 'zero_based_continuous', object_id: 2, source_key: '/shapes/1', source_id: null,
      group_id: null, label: 'person', bbox_origin: 'derived_from_polygon', bbox: [60, 20, 90, 70],
      polygons: [[[60, 20], [90, 20], [90, 70], [60, 70]]] },
  ];
}

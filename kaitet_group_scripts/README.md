# kaitet-group transfer scripts (reference copy)

Verbatim copies of the transfer-related **Server Scripts** and **Client Scripts** from the
live v15 site `https://kaitet-group.c.frappe.cloud`, pulled 2026-09-25 through the REST API.
The last column says where each one now lives in the v16 codebase.

This folder is reference only: it sits outside the `upande_quality` package and outside
`fixtures/` (every file there is imported on `bench migrate`), and is excluded from ruff so it
stays byte-for-byte what runs on the site. Each file has a comment header, then the original
script. `manifest.json` has the same index in machine-readable form.

## v16 field names

The live scripts use the v15 `custom_*` pick-row fields; v16 renamed them (compared field by
field against both sites' schemas): `custom_bucket` → `bucket`, `custom_awaiting_transfer` →
`awaiting_transfer`, `custom_loaded_in_trolley` → `loaded_in_trolley`, `custom_in_transit` →
`in_transit`, `custom_trolley_id` → `trolley_id`, `custom_transit_truck` → `transit_truck`,
`custom_shelf` → `shelf`, `custom_shelved` → `shelved`, `custom_stem_length` → `stem_length`
(Pick List Item); `custom_farm` → `farm`, `custom_team` → `team`, `custom_order_name` →
`order_name` (Order Pick List); `custom_farm` → `farm` (Stock Entry). v16 also leaves
`Pick List Item.warehouse` empty and fills `source_warehouse`, and the Order Pick List table
is `table_ytkc`, not `locations`. The live `custom_truck` (order's truck) has no v16
equivalent other than `transit_truck`, which pick-list creation pre-fills with it.

## Bucket transfers — 24 scripts

| Script | Type | Method / DocType | Enabled live | Lines | Where it lives on v16 |
|---|---|---|---|---|---|
| [Bucket Logistics Route Vehicle Filter](bucket_transfers/client/bucket_logistics_route_vehicle_filter.js) | Client | `Bucket Logistics Route` | yes | 8 | upande_quality/public/js/bucket_logistics_route.js (new) |
| [Block OPL Submit While Awaiting Transfer](bucket_transfers/server/block_opl_submit_while_awaiting_transfer.py) | DocType Event | `Order Pick List · Before Submit` | **off** | 15 | not ported — disabled live; v16 enforces it in upande_packhouse opl_submit_blockers |
| [Compute Farm Distance Via Farms](bucket_transfers/server/compute_farm_distance_via_farms.py) | DocType Event | `Farm Distance · Before Save` | yes | 69 | upande_quality/transfer_events.py + Custom Field Farm Distance-via_farms (new) |
| [Delete Bucket Trip](bucket_transfers/server/delete_bucket_trip.py) | API | `deleteBucketTrip` | yes | 8 | upande_packhouse/api/transfer_control.py (+ wrapper in upande_quality/mobile/api.py) |
| [Dispatch Bucket Trip](bucket_transfers/server/dispatch_bucket_trip.py) | API | `dispatchBucketTrip` | yes | 17 | upande_packhouse/mobile/api.py, api/transfer_control.py (+ wrapper in upande_quality/mobile/api.py) |
| [Fetch Transit Time](bucket_transfers/server/fetch_transit_time__older.py) | API | `fetch_transit_time` | yes | 53 | superseded by fetch_transit_time (same api_method) |
| [Fetch Transit Time Pivot Data](bucket_transfers/server/fetch_transit_time_pivot_data.py) | API | `fetchTransitTimePivotData` | yes | 371 | upande_quality fixtures/server_script.json (already v16) |
| [Get Bucket Logistics](bucket_transfers/server/get_bucket_logistics.py) | API | `getBucketLogistics` | yes | 118 | upande_packhouse/api/bucket_logistics.py (+ wrapper in upande_quality/mobile/api.py) |
| [Get Bucket Logistics Detail](bucket_transfers/server/get_bucket_logistics_detail.py) | API | `getBucketLogisticsDetail` | yes | 35 | upande_packhouse/api/bucket_logistics.py (+ wrapper in upande_quality/mobile/api.py) |
| [Get Driver Bucket Logistics](bucket_transfers/server/get_driver_bucket_logistics.py) | API | `getDriverBucketLogistics` | yes | 37 | upande_quality/mobile/api.py (new) |
| [Get Farm Planned Trips](bucket_transfers/server/get_farm_planned_trips.py) | API | `getFarmPlannedTrips` | yes | 242 | upande_quality/mobile/api.py (translated; reads source_warehouse) |
| [Get Saved Trolleys](bucket_transfers/server/get_saved_trolleys.py) | API | `getSavedTrolleys` | yes | 74 | upande_quality/mobile/api.py (translated; reads source_warehouse) |
| [Get Transfer Control Data](bucket_transfers/server/get_transfer_control_data.py) | API | `getTransferControlData` | yes | 137 | upande_packhouse/api/transfer_control.py (+ wrapper in upande_quality/mobile/api.py) |
| [Get Transfer Schedule Data](bucket_transfers/server/get_transfer_schedule_data.py) | API | `getTransferScheduleData` | yes | 369 | upande_packhouse/mobile/api.py, api/transfer_control.py (+ wrapper in upande_quality/mobile/api.py) |
| [Load Trolley In Truck](bucket_transfers/server/load_trolley_in_truck.py) | API | `loadTrolleyInTruck` | yes | 92 | upande_quality/mobile/api.py (translated) |
| [Receive Bucket Trip](bucket_transfers/server/receive_bucket_trip.py) | API | `receiveBucketTrip` | yes | 17 | upande_packhouse/mobile/api.py, api/transfer_control.py (+ wrapper in upande_quality/mobile/api.py) |
| [Save Bucket Logistics Route](bucket_transfers/server/save_bucket_logistics_route.py) | API | `saveBucketLogisticsRoute` | yes | 60 | upande_packhouse/api/transfer_control.py (+ wrapper in upande_quality/mobile/api.py) |
| [Save Bucket Trip](bucket_transfers/server/save_bucket_trip.py) | API | `saveBucketTrip` | yes | 91 | upande_packhouse/api/transfer_control.py (+ wrapper in upande_quality/mobile/api.py) |
| [Save Trolley Data](bucket_transfers/server/save_trolley_data.py) | API | `saveTrolleyData` | yes | 74 | upande_quality/mobile/api.py (translated to v16 fields) |
| [deleteSavedTrolleys](bucket_transfers/server/deletesavedtrolleys.py) | API | `deleteSavedTrolleys` | yes | 97 | upande_quality/mobile/api.py (translated) |
| [fetch_transit_time](bucket_transfers/server/fetch_transit_time.py) | API | `fetch_transit_time` | yes | 39 | upande_quality fixtures/server_script.json (already v16: links by bucket id) |
| [fixTransferShelvedOpls](bucket_transfers/server/fixtransfershelvedopls.py) | API | `fixTransferShelvedOpls` | yes | 73 | upande_quality/mobile/api.py (new) |
| [getInTransitBuckets](bucket_transfers/server/getintransitbuckets.py) | API | `getInTransitBuckets` | yes | 97 | upande_quality/mobile/api.py (already v16) |
| [setOfflineTrolleyFlags](bucket_transfers/server/setofflinetrolleyflags.py) | API | `setOfflineTrolleyFlags` | yes | 104 | upande_quality/mobile/api.py (translated) |

## Other scripts matching "transfer" — 18 scripts

Picked up by the keyword filter but not part of the bucket flow (customer Delivery Trips,
GPS, KPI strips, notifications, Shopify, a Lead field).

| Script | Type | Method / DocType | Enabled live | Lines | Where it lives on v16 |
|---|---|---|---|---|---|
| [Autopopulate Item Fields](other/client/autopopulate_item_fields.js) | Client | `Daily Shopify Transfer` | yes | 94 | not ported — Daily Shopify Transfer doesn't exist on v16 |
| [Copy From Last Transfer Button](other/client/copy_from_last_transfer_button.js) | Client | `Daily Shopify Transfer` | yes | 17 | not ported — Daily Shopify Transfer doesn't exist on v16 |
| [Flower Material Transfer Listview Custom Status](other/client/flower_material_transfer_listview_custom_status.js) | Client | `Stock Entry` | yes | 34 | not ported — custom_allocation_status / custom_sales_order don't exist on Stock Entry in v16 |
| [Initiate Shopify Stock Transfer](other/client/initiate_shopify_stock_transfer.js) | Client | `Daily Shopify Transfer` | yes | 30 | not ported — Daily Shopify Transfer doesn't exist on v16 |
| [Reverse Transfer Button](other/client/reverse_transfer_button.js) | Client | `Daily Shopify Transfer` | yes | 41 | not ported — Daily Shopify Transfer doesn't exist on v16 |
| [Transfer Grading Stock](other/client/transfer_grading_stock.js) | Client | `Stock Entry` | **off** | 41 | not ported — disabled on the live site |
| [Transfer Linked City field to Standard City Field](other/client/transfer_linked_city_field_to_standard_city_field.js) | Client | `Lead` | **off** | 9 | not ported — disabled on the live site |
| [Trip Button](other/client/trip_button.js) | Client | `Delivery Trip` | **off** | 33 | not ported — disabled on the live site |
| [Create delivery trip](other/server/create_delivery_trip.py) | DocType Event | `Delivery Note · Before Save` | **off** | 75 | not ported — disabled on the live site |
| [End Trip Transfer](other/server/end_trip_transfer.py) | API | `end_trip_transfer` | **off** | 43 | not ported — disabled on the live site |
| [Get Vehicle Trips](other/server/get_vehicle_trips.py) | API | `get_vehicle_trips` | yes | 263 | upande_quality/mobile/api.py get_vehicle_trips (new) |
| [KPI Strip API](other/server/kpi_strip_api.py) | API | `kpi_strip` | yes | 90 | not ported — Avocado Harvest Plan / Crate Allocation don't exist on v16 |
| [Latest Routes](other/server/latest_routes.py) | API | `latestRoutes` | yes | 39 | upande_quality/mobile/api.py latestRoutes (new) |
| [Material Request (Transfer) Notification](other/server/material_request_transfer_notification.py) | DocType Event | `Material Request · After Submit` | **off** | 33 | not ported — disabled on the live site |
| [Material Transfer Transit Notification](other/server/material_transfer_transit_notification.py) | DocType Event | `Stock Entry · After Submit` | yes | 251 | upande_quality/transfer_events.py (new) |
| [Pallet KPI Strip API](other/server/pallet_kpi_strip_api.py) | API | `pallet_kpi_strip` | yes | 32 | not ported — Pallet Record doesn't exist on v16 |
| [Start Trip Transfer](other/server/start_trip_transfer.py) | API | `start_trip_transfer` | **off** | 41 | not ported — disabled on the live site |
| [Transfer Receipt Notifications](other/server/transfer_receipt_notifications.py) | DocType Event | `Stock Entry · After Submit` | **off** | 164 | not ported — disabled on the live site |

# Name: Compute Farm Distance Via Farms
# Type: Server Script · DocType Event
# DocType: Farm Distance
# Event: Before Save
# Enabled: yes
# Modified: 2026-08-07 16:21:29
# Source: https://kaitet-group.c.frappe.cloud (live), pulled 2026-09-25 — verbatim, not wired up
# ----------------------------------------------------------------------
# Frappe Server Script (Type: DocType Event), doctype=Farm Distance, event=Before Save
# Auto-computes `via_farms` for every record: the REAL farms this pair's drive passes
# through, derived from the road-leg graph (is_road_leg=1 records) — never hardcoded
# to specific farm names. If more farms/roads are added to Farm Distance later, this
# recomputes correctly with zero code changes, because it re-reads the graph fresh
# every save. A direct road (is_road_leg=1) passes through nothing -> blank.
# safe_exec: no imports/def — plain lists/dicts/while loops, index-based queue
# (avoid list.pop(), stick to the .append() pattern already proven to work here).

if doc.is_road_leg:
    doc.via_farms = ""
else:
    leg_rows = frappe.get_all(
        "Farm Distance",
        filters={"is_road_leg": 1},
        fields=["from_farm", "to_farm"],
    )
    # adjacency list over the REAL road graph only
    adj = {}
    i = 0
    while i < len(leg_rows):
        a = leg_rows[i].get("from_farm")
        b = leg_rows[i].get("to_farm")
        if a and b:
            if a not in adj:
                adj[a] = []
            if b not in adj:
                adj[b] = []
            adj[a].append(b)
            adj[b].append(a)
        i = i + 1

    # BFS from doc.from_farm to doc.to_farm (index-based queue, no .pop()) — the
    # network is a tree so there is exactly one path; still correct if it is ever
    # extended into a general graph (BFS finds a shortest path in hop count).
    start = doc.from_farm
    target = doc.to_farm
    queue = [[start]]
    qi = 0
    seen = {}
    seen[start] = 1
    path = None
    while qi < len(queue) and path is None:
        cur = queue[qi]
        qi = qi + 1
        node = cur[len(cur) - 1]
        neighbors = adj.get(node) or []
        j = 0
        while j < len(neighbors):
            nb = neighbors[j]
            if nb not in seen:
                seen[nb] = 1
                new_path = cur + [nb]
                if nb == target:
                    path = new_path
                else:
                    queue.append(new_path)
            j = j + 1

    if path:
        # intermediate farms only — exclude the two endpoints themselves
        via = []
        k = 1
        while k < len(path) - 1:
            via.append(path[k])
            k = k + 1
        doc.via_farms = ", ".join(via)
    else:
        doc.via_farms = ""

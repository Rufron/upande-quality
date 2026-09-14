import json

import frappe
from frappe.utils import cint, date_diff, flt, getdate

# Vaselife Report
# ----------------
# One row per Vaselife Sample. Header data comes from `Vaselife Sample`; the
# per-day failure data comes from the `Vaselife Observation` records linked to
# that sample. Layout mirrors the "Vaselife Template" QC spreadsheet.
#
# Computed metrics (per sample), where
#   N   = sampled stems (Vaselife Sample.no_of_stems)
#   cum = cumulative stems failed up to and including a given day
#   pct = cum / N * 100
#
#   1. Average Vaselife  -> the day cum failure first reaches >= 50% of N.
#                           (Vase life is "stopped" once half the stems fail.)
#   2. VL 10%            -> the day pct first exceeds 10%.
#   3. VL 20%            -> the day pct first exceeds 20%.
#   4. First Stem Failure-> the first observation day with any stem failed.
#   5. Max Cut Stage     -> highest cut stage achieved. Per-observation cut
#                           stage is not captured yet, so this currently falls
#                           back to the sample's Initial Cut Stage.
#
# "Day N" is counted from the Vase Date (day 1 = vase date). If a sample has no
# vase date, the earliest observation date is treated as day 1.


def execute(filters=None):
    filters = filters or {}
    data, max_day = get_data(filters)
    columns = get_columns(max_day)
    return columns, data


SAMPLE_FIELDS = [
    "name",
    "sampling_date",
    "consignment",
    "supermarket_date",
    "du_date",
    "vase_date",
    "variety",
    "breeder",
    "commercial_status",
    "crop",
    "harvest_date",
    "harvest_time",
    "line_code",
    "farm",
    "gh",
    "length",
    "no_of_stems",
    "bud_height",
    "bud_width",
    "initial_cut_stage",
    "prepared_by",
]


def build_sample_filters(filters):
    f = {}

    from_date = filters.get("from_date")
    to_date = filters.get("to_date")
    if from_date and to_date:
        f["sampling_date"] = ["between", [from_date, to_date]]
    elif from_date:
        f["sampling_date"] = [">=", from_date]
    elif to_date:
        f["sampling_date"] = ["<=", to_date]

    if filters.get("variety"):
        f["variety"] = ["like", "%{0}%".format(filters["variety"])]
    if filters.get("breeder"):
        f["breeder"] = ["like", "%{0}%".format(filters["breeder"])]
    if filters.get("farm"):
        f["farm"] = filters["farm"]
    if filters.get("commercial_status"):
        f["commercial_status"] = filters["commercial_status"]
    if filters.get("crop"):
        f["crop"] = filters["crop"]

    return f


def parse_reasons(raw):
    """Normalise a Vaselife Observation.failure_reasons JSON blob into a list of
    (reason_name, count) tuples. Handles a plain list of strings, a list of
    dicts, or a single dict."""
    if not raw:
        return []
    try:
        data = json.loads(raw)
    except (ValueError, TypeError):
        return []

    if isinstance(data, dict):
        data = [data]
    if not isinstance(data, list):
        return []

    out = []
    for item in data:
        if isinstance(item, str):
            name = item.strip()
            if name:
                out.append((name, 1))
        elif isinstance(item, dict):
            name = (
                item.get("name")
                or item.get("reason")
                or item.get("parameter")
                or item.get("label")
            )
            if not name:
                continue
            cnt = item.get("count") or item.get("qty") or item.get("stems") or 1
            try:
                cnt = int(cnt)
            except (ValueError, TypeError):
                cnt = 1
            out.append((str(name).strip(), cnt))
    return out


def day_number(obs_date, base_date):
    """Day index of an observation, counting the vase/base date as day 1."""
    diff = date_diff(obs_date, base_date)
    return diff + 1 if diff >= 0 else 1


def cut_stage_value(raw):
    """Numeric sort key for a cut-stage label so stages can be compared.
    Handles the open-ended labels '<1.5' and '5.0>' as well as plain numbers
    (the sample's Initial Cut Stage uses '1'..'5')."""
    if raw is None:
        return None
    s = str(raw).strip()
    if not s:
        return None
    try:
        if s.startswith("<"):
            return flt(s[1:]) - 0.01
        if s.endswith(">"):
            return flt(s[:-1]) + 0.01
        return flt(s)
    except (ValueError, TypeError):
        return None


def get_data(filters):
    samples = frappe.get_all(
        "Vaselife Sample",
        filters=build_sample_filters(filters),
        fields=SAMPLE_FIELDS,
        order_by="sampling_date desc, name desc",
    )
    if not samples:
        return [], 0

    names = [s.name for s in samples]

    observations = frappe.get_all(
        "Vaselife Observation",
        filters={"sample": ["in", names]},
        fields=["name", "sample", "date", "stems_failed", "cut_stage", "failure_reasons"],
        order_by="date asc",
    )

    # Failure reasons now live in the Vaselife Failure Reason child table;
    # fall back to the legacy JSON blob for any un-migrated observation.
    reasons_by_obs = {}
    obs_names = [o.name for o in observations]
    if obs_names:
        for r in frappe.get_all(
            "Vaselife Failure Reason",
            filters={"parenttype": "Vaselife Observation", "parent": ["in", obs_names]},
            fields=["parent", "reason", "stems"],
        ):
            reasons_by_obs.setdefault(r.parent, []).append((r.reason, cint(r.stems)))

    obs_by_sample = {}
    for o in observations:
        obs_by_sample.setdefault(o.sample, []).append(o)

    rows = []
    max_day = 0

    for s in samples:
        row = dict(s)
        n = cint(s.no_of_stems)
        row["sampled_stems"] = n

        if s.sampling_date and s.harvest_date:
            row["stock_age"] = date_diff(s.sampling_date, s.harvest_date)

        obs = sorted(
            obs_by_sample.get(s.name, []),
            key=lambda x: getdate(x.date),
        )

        base_date = None
        if s.vase_date:
            base_date = getdate(s.vase_date)
        elif obs:
            base_date = getdate(obs[0].date)

        cumulative = 0
        total_failed = 0
        first_failure = None
        vl_10 = vl_20 = avg_vaselife = None
        reason_counts = {}

        # "Max cut stage achieved" = highest cut stage across the sample's own
        # initial stage and every daily observation.
        best_cut_val = cut_stage_value(s.initial_cut_stage)
        best_cut_label = s.initial_cut_stage if best_cut_val is not None else None

        for o in obs:
            failed = cint(o.stems_failed)
            total_failed += failed

            cv = cut_stage_value(o.cut_stage)
            if cv is not None and (best_cut_val is None or cv > best_cut_val):
                best_cut_val = cv
                best_cut_label = o.cut_stage

            dnum = day_number(o.date, base_date) if base_date else 1
            if dnum > max_day:
                max_day = dnum
            key = "day_{0}".format(dnum)
            row[key] = cint(row.get(key)) + failed

            if failed > 0 and first_failure is None:
                first_failure = dnum

            cumulative += failed
            pct = (cumulative / n * 100.0) if n else 0.0
            if vl_10 is None and pct > 10:
                vl_10 = dnum
            if vl_20 is None and pct > 20:
                vl_20 = dnum
            if avg_vaselife is None and pct >= 50:
                avg_vaselife = dnum

            child = reasons_by_obs.get(o.name)
            # Aggregate by the stems count recorded per reason (child table).
            # Legacy JSON rows fall back to occurrence counting.
            pairs = [(rn, sc) for rn, sc in child] if child else parse_reasons(o.failure_reasons)
            for rname, rcount in pairs:
                reason_counts[rname] = reason_counts.get(rname, 0) + rcount

        row["total_failed"] = total_failed
        row["pct_failed"] = round(total_failed / n * 100.0, 1) if n else 0
        row["first_failure"] = first_failure
        row["vl_10"] = vl_10
        row["vl_20"] = vl_20
        row["avg_vaselife"] = avg_vaselife
        row["max_cut_stage"] = best_cut_label
        row["reasons"] = ", ".join(
            "{0} ({1})".format(k, v)
            for k, v in sorted(reason_counts.items(), key=lambda kv: (-kv[1], kv[0]))
        )

        rows.append(row)

    return rows, max_day


def get_columns(max_day):
    columns = [
        {"fieldname": "name", "label": "Sample", "fieldtype": "Link", "options": "Vaselife Sample", "width": 130},
        {"fieldname": "sampling_date", "label": "Sampling Date", "fieldtype": "Date", "width": 100},
        {"fieldname": "variety", "label": "Variety", "fieldtype": "Data", "width": 140},
        {"fieldname": "breeder", "label": "Breeder", "fieldtype": "Data", "width": 110},
        {"fieldname": "crop", "label": "Crop", "fieldtype": "Data", "width": 90},
        {"fieldname": "commercial_status", "label": "Commercial Status", "fieldtype": "Data", "width": 120},
        {"fieldname": "farm", "label": "Farm", "fieldtype": "Link", "options": "Farm", "width": 100},
        {"fieldname": "gh", "label": "GH", "fieldtype": "Data", "width": 70},
        {"fieldname": "line_code", "label": "Line Code", "fieldtype": "Data", "width": 90},
        {"fieldname": "consignment", "label": "Consignment", "fieldtype": "Data", "width": 110},
        {"fieldname": "harvest_date", "label": "Harvest Date", "fieldtype": "Date", "width": 100},
        {"fieldname": "harvest_time", "label": "Harvest Time", "fieldtype": "Data", "width": 90},
        {"fieldname": "vase_date", "label": "Vase Date", "fieldtype": "Date", "width": 100},
        {"fieldname": "supermarket_date", "label": "Supermarket Date", "fieldtype": "Date", "width": 110},
        {"fieldname": "du_date", "label": "DU Date", "fieldtype": "Date", "width": 90},
        {"fieldname": "length", "label": "Length (cm)", "fieldtype": "Float", "width": 90, "precision": 1},
        {"fieldname": "sampled_stems", "label": "Sampled Stems", "fieldtype": "Int", "width": 100},
        {"fieldname": "bud_height", "label": "Height (mm)", "fieldtype": "Float", "width": 90, "precision": 1},
        {"fieldname": "bud_width", "label": "Width (mm)", "fieldtype": "Float", "width": 90, "precision": 1},
        {"fieldname": "initial_cut_stage", "label": "Sampled Cut Stage", "fieldtype": "Data", "width": 110},
        {"fieldname": "stock_age", "label": "Stock Age (days)", "fieldtype": "Int", "width": 100},
        {"fieldname": "max_cut_stage", "label": "Max Cut Stage", "fieldtype": "Data", "width": 100},
        {"fieldname": "avg_vaselife", "label": "Avg Vaselife (day → 50%)", "fieldtype": "Int", "width": 150},
        {"fieldname": "vl_10", "label": "VL 10% (day)", "fieldtype": "Int", "width": 100},
        {"fieldname": "vl_20", "label": "VL 20% (day)", "fieldtype": "Int", "width": 100},
        {"fieldname": "first_failure", "label": "First Failure (day)", "fieldtype": "Int", "width": 120},
        {"fieldname": "total_failed", "label": "Total Failed", "fieldtype": "Int", "width": 90},
        {"fieldname": "pct_failed", "label": "% Failed", "fieldtype": "Percent", "width": 90},
    ]

    for d in range(1, max_day + 1):
        columns.append(
            {"fieldname": "day_{0}".format(d), "label": "Day {0}".format(d), "fieldtype": "Int", "width": 70}
        )

    columns.append({"fieldname": "reasons", "label": "Failure Reasons", "fieldtype": "Data", "width": 280})
    columns.append({"fieldname": "prepared_by", "label": "Prepared By", "fieldtype": "Data", "width": 120})

    return columns

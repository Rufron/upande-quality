# Copyright (c) 2026, Upande and contributors
# For license information, please see license.txt

from upande_quality.quality_v2 import page_context

no_cache = 1


def get_context(context):
	return page_context(context, "quality-raw-data", "Quality Raw Data")

from upande_quality.quality_v2 import page_context

no_cache = 1


def get_context(context):
	return page_context(context, "solution-mixing-overview", "Solution Mixing")

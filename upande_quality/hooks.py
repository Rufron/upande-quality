app_name = "upande_quality"
app_title = "Upande Quality"
app_publisher = "Adminstrator"
app_description = "Upande Quality"
app_email = "dev@upande.com"
app_license = "mit"

# Apps
# ------------------

# Quality Reporting / Corrective Action Report link into upande_kaitet
# (Farm, Intake Quality Parameter), so kaitet must be installed first.
required_apps = ["upande_kaitet"]

# Each item in the list will be shown as an app in the apps page
# add_to_apps_screen = [
# 	{
# 		"name": "upande_quality",
# 		"logo": "/assets/upande_quality/logo.png",
# 		"title": "Upande Quality",
# 		"route": "/upande_quality",
# 		"has_permission": "upande_quality.api.permission.has_app_permission"
# 	}
# ]

# Fixtures
# ------------------
# The Upande Quality workspace renders the "QC" Custom HTML Block; that block
# lives only in the DB unless exported here, so ship it so the workspace is not
# blank on deployed sites.
fixtures = [
	{"doctype": "Custom HTML Block", "filters": [["name", "in", ["QC"]]]},
	# QC Control Point master (seeded control points: Intake, Packhouse) — the
	# control_point Link on Quality Reporting / CAR resolves against these.
	{"doctype": "QC Control Point"},
	# Field-visibility gating (eval depends_on) for the QC intake stages on the
	# shared Stock Entry doctype + Quality Reporting Intake/Packhouse layout.
	# Filtered to property in (depends_on, reqd) so ONLY our customizations ship,
	# not other apps' Stock Entry property setters.
	{
		"doctype": "Property Setter",
		"filters": [
			["doc_type", "in", ["Quality Reporting", "Stock Entry"]],
			["property", "in", ["depends_on", "reqd"]],
		],
	},
]

# Includes in <head>
# ------------------

# include js, css files in header of desk.html
# app_include_css = "/assets/upande_quality/css/upande_quality.css"
# app_include_js = "/assets/upande_quality/js/upande_quality.js"

# include js, css files in header of web template
# web_include_css = "/assets/upande_quality/css/upande_quality.css"
# web_include_js = "/assets/upande_quality/js/upande_quality.js"

# include custom scss in every website theme (without file extension ".scss")
# website_theme_scss = "upande_quality/public/scss/website"

# include js, css files in header of web form
# webform_include_js = {"doctype": "public/js/doctype.js"}
# webform_include_css = {"doctype": "public/css/doctype.css"}

# include js in page
# page_js = {"page" : "public/js/file.js"}

# include js in doctype views
# doctype_js = {"doctype" : "public/js/doctype.js"}
# doctype_list_js = {"doctype" : "public/js/doctype_list.js"}
# doctype_tree_js = {"doctype" : "public/js/doctype_tree.js"}
# doctype_calendar_js = {"doctype" : "public/js/doctype_calendar.js"}

# Svg Icons
# ------------------
# include app icons in desk
# app_include_icons = "upande_quality/public/icons.svg"

# Home Pages
# ----------

# application home page (will override Website Settings)
# home_page = "login"

# website user home page (by Role)
# role_home_page = {
# 	"Role": "home_page"
# }

# Generators
# ----------

# automatically create page for each record of this doctype
# website_generators = ["Web Page"]

# automatically load and sync documents of this doctype from downstream apps
# importable_doctypes = [doctype_1]

# Jinja
# ----------

# add methods and filters to jinja environment
# jinja = {
# 	"methods": "upande_quality.utils.jinja_methods",
# 	"filters": "upande_quality.utils.jinja_filters"
# }

# Installation
# ------------

# before_install = "upande_quality.install.before_install"
# after_install = "upande_quality.install.after_install"

# Uninstallation
# ------------

# before_uninstall = "upande_quality.uninstall.before_uninstall"
# after_uninstall = "upande_quality.uninstall.after_uninstall"

# Integration Setup
# ------------------
# To set up dependencies/integrations with other apps
# Name of the app being installed is passed as an argument

# before_app_install = "upande_quality.utils.before_app_install"
# after_app_install = "upande_quality.utils.after_app_install"

# Integration Cleanup
# -------------------
# To clean up dependencies/integrations with other apps
# Name of the app being uninstalled is passed as an argument

# before_app_uninstall = "upande_quality.utils.before_app_uninstall"
# after_app_uninstall = "upande_quality.utils.after_app_uninstall"

# Build
# ------------------
# To hook into the build process

# after_build = "upande_quality.build.after_build"

# Desk Notifications
# ------------------
# See frappe.core.notifications.get_notification_config

# notification_config = "upande_quality.notifications.get_notification_config"

# Permissions
# -----------
# Permissions evaluated in scripted ways

# permission_query_conditions = {
# 	"Event": "frappe.desk.doctype.event.event.get_permission_query_conditions",
# }
#
# has_permission = {
# 	"Event": "frappe.desk.doctype.event.event.has_permission",
# }

# Document Events
# ---------------
# Hook on document methods and events

# doc_events = {
# 	"*": {
# 		"on_update": "method",
# 		"on_cancel": "method",
# 		"on_trash": "method"
# 	}
# }

# Scheduled Tasks
# ---------------

# scheduler_events = {
# 	"all": [
# 		"upande_quality.tasks.all"
# 	],
# 	"daily": [
# 		"upande_quality.tasks.daily"
# 	],
# 	"hourly": [
# 		"upande_quality.tasks.hourly"
# 	],
# 	"weekly": [
# 		"upande_quality.tasks.weekly"
# 	],
# 	"monthly": [
# 		"upande_quality.tasks.monthly"
# 	],
# }

# Testing
# -------

# before_tests = "upande_quality.install.before_tests"

# Extend DocType Class
# ------------------------------
#
# Specify custom mixins to extend the standard doctype controller.
# extend_doctype_class = {
# 	"Task": "upande_quality.custom.task.CustomTaskMixin"
# }

# Overriding Methods
# ------------------------------
#
# override_whitelisted_methods = {
# 	"frappe.desk.doctype.event.event.get_events": "upande_quality.event.get_events"
# }
#
# each overriding function accepts a `data` argument;
# generated from the base implementation of the doctype dashboard,
# along with any modifications made in other Frappe apps
# override_doctype_dashboards = {
# 	"Task": "upande_quality.task.get_dashboard_data"
# }

# exempt linked doctypes from being automatically cancelled
#
# auto_cancel_exempted_doctypes = ["Auto Repeat"]

# Ignore links to specified DocTypes when deleting documents
# -----------------------------------------------------------

# ignore_links_on_delete = ["Communication", "ToDo"]

# Request Events
# ----------------
# before_request = ["upande_quality.utils.before_request"]
# after_request = ["upande_quality.utils.after_request"]

# Job Events
# ----------
# before_job = ["upande_quality.utils.before_job"]
# after_job = ["upande_quality.utils.after_job"]

# User Data Protection
# --------------------

# user_data_fields = [
# 	{
# 		"doctype": "{doctype_1}",
# 		"filter_by": "{filter_by}",
# 		"redact_fields": ["{field_1}", "{field_2}"],
# 		"partial": 1,
# 	},
# 	{
# 		"doctype": "{doctype_2}",
# 		"filter_by": "{filter_by}",
# 		"partial": 1,
# 	},
# 	{
# 		"doctype": "{doctype_3}",
# 		"strict": False,
# 	},
# 	{
# 		"doctype": "{doctype_4}"
# 	}
# ]

# Authentication and authorization
# --------------------------------

# auth_hooks = [
# 	"upande_quality.auth.validate"
# ]

# Automatically update python controller files with type annotations for this app.
# export_python_type_annotations = True

# default_log_clearing_doctypes = {
# 	"Logging DocType Name": 30  # days to retain logs
# }

# Translation
# ------------
# List of apps whose translatable strings should be excluded from this app's translations.
# ignore_translatable_strings_from = []


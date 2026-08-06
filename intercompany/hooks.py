app_name = "intercompany"
app_title = "Intercompany"
app_publisher = "jyothi.p@quarkcs.com"
app_description = "this is for intercompany transactions"
app_email = "jyothi.p@quarkcs.com"
app_license = "MIT"

# Includes in <head>
# ------------------

# include js, css files in header of desk.html
# app_include_css = "/assets/intercompany/css/intercompany.css"
# app_include_js = "/assets/intercompany/js/intercompany.js"

# include js, css files in header of web template
# web_include_css = "/assets/intercompany/css/intercompany.css"
# web_include_js = "/assets/intercompany/js/intercompany.js"

# include custom scss in every website theme (without file extension ".scss")
# website_theme_scss = "intercompany/public/scss/website"

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

# Jinja
# ----------

# add methods and filters to jinja environment
# jinja = {
# 	"methods": "intercompany.utils.jinja_methods",
# 	"filters": "intercompany.utils.jinja_filters"
# }

# Installation
# ------------

# before_install = "intercompany.install.before_install"
# after_install = "intercompany.install.after_install"

# Uninstallation
# ------------

# before_uninstall = "intercompany.uninstall.before_uninstall"
# after_uninstall = "intercompany.uninstall.after_uninstall"

# Integration Setup
# ------------------
# To set up dependencies/integrations with other apps
# Name of the app being installed is passed as an argument

# before_app_install = "intercompany.utils.before_app_install"
# after_app_install = "intercompany.utils.after_app_install"

# Integration Cleanup
# -------------------
# To clean up dependencies/integrations with other apps
# Name of the app being uninstalled is passed as an argument

# before_app_uninstall = "intercompany.utils.before_app_uninstall"
# after_app_uninstall = "intercompany.utils.after_app_uninstall"

# Desk Notifications
# ------------------
# See frappe.core.notifications.get_notification_config

# notification_config = "intercompany.notifications.get_notification_config"

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

# DocType Class
# ---------------
# Override standard doctype classes

# override_doctype_class = {
# 	"ToDo": "custom_app.overrides.CustomToDo"
# }

# Document Events
# ---------------
# Hook on document methods and events

doc_events = {
    "Sales Order": {
        "on_submit": "intercompany.intercompany.overrides.sales_order.create_ic_transaction",
        "on_cancel": "intercompany.intercompany.overrides.sales_order.reverse_ic_transaction",
    },
    "Sales Invoice": {
        "on_submit": "intercompany.intercompany.overrides.sales_invoice.create_ic_transaction",
        "on_cancel": "intercompany.intercompany.overrides.sales_invoice.reverse_ic_transaction",
    },
    "Delivery Note": {
        "on_submit": "intercompany.intercompany.overrides.delivery_note.create_ic_transaction",
        "on_cancel": "intercompany.intercompany.overrides.delivery_note.reverse_ic_transaction",
    },
    "Journal Entry": {
        "on_submit": "intercompany.intercompany.overrides.journal_entry.create_ic_transaction",
        "on_cancel": "intercompany.intercompany.overrides.journal_entry.reverse_ic_transaction",
    },
}

# The ledger only ever *observes* the documents it references — a submitted entry
# must never block cancelling or deleting the source or counter document it points
# at. Same category frappe puts Communication, Version and ToDo in.
ignore_links_on_delete = ["Intercompany Ledger"]

permission_query_conditions = {
    "Intercompany Ledger": "intercompany.intercompany.permissions.ledger_query_conditions",
    "Intercompany Rule": "intercompany.intercompany.permissions.relationship_query_conditions",
}
# Scheduled Tasks
# ---------------

# scheduler_events = {
# 	"all": [
# 		"intercompany.tasks.all"
# 	],
# 	"daily": [
# 		"intercompany.tasks.daily"
# 	],
# 	"hourly": [
# 		"intercompany.tasks.hourly"
# 	],
# 	"weekly": [
# 		"intercompany.tasks.weekly"
# 	],
# 	"monthly": [
# 		"intercompany.tasks.monthly"
# 	],
# }

# Testing
# -------

# before_tests = "intercompany.install.before_tests"

# Overriding Methods
# ------------------------------
#
# override_whitelisted_methods = {
# 	"frappe.desk.doctype.event.event.get_events": "intercompany.event.get_events"
# }
#
# each overriding function accepts a `data` argument;
# generated from the base implementation of the doctype dashboard,
# along with any modifications made in other Frappe apps
# override_doctype_dashboards = {
# 	"Task": "intercompany.task.get_dashboard_data"
# }

# exempt linked doctypes from being automatically cancelled
#
# auto_cancel_exempted_doctypes = ["Auto Repeat"]

# Ignore links to specified DocTypes when deleting documents
# -----------------------------------------------------------

# ignore_links_on_delete = ["Communication", "ToDo"]

# Request Events
# ----------------
# before_request = ["intercompany.utils.before_request"]
# after_request = ["intercompany.utils.after_request"]

# Job Events
# ----------
# before_job = ["intercompany.utils.before_job"]
# after_job = ["intercompany.utils.after_job"]

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
# 	"intercompany.auth.validate"
# ]


fixtures = [
    {
        "doctype": "Custom Field",
        "filters": [["name", "in", [
            "Purchase Invoice-custom_intercompany_reference",
            "Purchase Receipt-custom_intercompany_reference",
            "Purchase Order-custom_intercompany_reference",
            "Delivery Note-custom_intercompany_reference",
        ]]],
    },
    {
        "doctype": "Role",
        "filters": [["name", "in", ["Intercompany User", "Intercompany Approver"]]],
    },
    {
        "doctype": "Number Card",
        "filters": [["name", "in", [
            "Active Relationships", "Pending Inbox", "Auto-Posted Today", "Failed Postings",
        ]]],
    },
    {
        "doctype": "Workspace",
        "filters": [["name", "=", "Intercompany"]],
    },
    {
        "doctype": "Custom HTML Block",
        "filters": [["name", "in", ["Intercompany Ledger Activity"]]],
    },
]
import frappe, json

with open("/home/acube/Hanayen-bench/apps/intercompany/intercompany/fixtures/custom_html_block.json") as fh:
    block_data = json.load(fh)[0]

doc = frappe.get_doc("Custom HTML Block", block_data["name"])
doc.html = block_data["html"]
doc.script = block_data["script"]
doc.save(ignore_permissions=True)
frappe.db.commit()

ws = frappe.get_doc("Workspace", "Intercompany")
content = json.loads(ws.content)
seen = set()
deduped = []
for blk in content:
    if blk.get("type") == "custom_block":
        key = blk.get("data", {}).get("custom_block_name")
        if key in seen:
            continue
        seen.add(key)
    deduped.append(blk)
ws.content = json.dumps(deduped)
for sc in ws.shortcuts:
    if sc.link_to == "Intercompany Rule":
        sc.stats_filter = None
ws.save(ignore_permissions=True)
frappe.db.commit()

print("Done. Hard-reload the workspace (Ctrl+Shift+R).")

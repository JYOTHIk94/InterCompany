// Copyright (c) 2026, jyothi.p@quarkcs.com and contributors
// For license information, please see license.txt

frappe.ui.form.on('Intercompany Ledger', {
	refresh(frm) {
		// Accept/reject belong to the routable Transaction row only — Event rows
		// are frozen audit records.
		if (frm.doc.entry_type === 'Transaction' && frm.doc.status === 'Pending') {
			frm.add_custom_button(__('Accept'), () => {
				frm.call('accept').then(() => frm.reload_doc());
			}).addClass('btn-success');

			frm.add_custom_button(__('Reject'), () => {
				frappe.prompt(
					[{ fieldname: 'reason', fieldtype: 'Small Text', label: 'Reason' }],
					(values) => {
						frm.call('reject', { reason: values.reason }).then(() => frm.reload_doc());
					},
					__('Reject ledger entry')
				);
			});
		}

		if (frm.doc.target_doctype && frm.doc.target_name) {
			frm.add_custom_button(__('Open counter-doc'), () => {
				frappe.set_route('Form', frm.doc.target_doctype, frm.doc.target_name);
			});
		}

		if (frm.doc.source_doctype && frm.doc.source_name) {
			frm.add_custom_button(__('Open source doc'), () => {
				frappe.set_route('Form', frm.doc.source_doctype, frm.doc.source_name);
			});
		}
	},
});

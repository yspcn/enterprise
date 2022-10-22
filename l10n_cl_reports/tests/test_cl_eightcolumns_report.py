# -*- coding: utf-8 -*-
from odoo.addons.account_reports.tests.common import TestAccountReportsCommon
from odoo import fields
from odoo.tests import tagged


@tagged('post_install', '-at_install')
class TestClEightColumnsReport(TestAccountReportsCommon):

    @classmethod
    def setUpClass(cls, chart_template_ref='l10n_cl.cl_chart_template'):
        super().setUpClass(chart_template_ref=chart_template_ref)

        invoice = cls.env['account.move'].create({
            'move_type': 'out_invoice',
            'partner_id': cls.partner_a.id,
            'invoice_date': '2017-01-01',
            'date': '2017-01-01',
            'invoice_line_ids': [(0, 0, {
                'product_id': cls.product_a.id,
                'tax_ids': [(6, 0, cls.company_data['default_tax_sale'].ids)],
                'quantity': 1.0,
                'price_unit': 1000.0,
            })],
        })
        invoice.action_post()

    def test_whole_report(self):
        report = self.env['account.eightcolumns.report.cl']
        options = self._init_options(report, fields.Date.from_string('2017-01-01'), fields.Date.from_string('2017-12-31'))

        self.assertLinesValues(
            report._get_lines(options),
            #   Cuenta                                  Debe            Haber   Deudor  Acreedor    Activo  Pasivo  Perdida Ganancia
            [   0,                                      1,              2,      3,      4,          5,      6,      7,      8],
            [
                ('110310 Clientes',                     1190.0,         0.0,    1190.0, 0.0,        1190.0, 0.0,    0.0,    0.0),
                ('210710 IVA Débito Fiscal',            0.0,            190.0,  0.0,    190.0,      0.0,    190.0,  0.0,    0.0),
                ('310110 Ingresos por Consultoría',     0.0,            1000.0, 0.0,    1000.0,     0.0,    0.0,    0.0,    1000.0),
                ('Subtotal',                            1190.0,         1190.0, 1190.0, 1190.0,     1190.0, 190.0,  0.0,    1000.0),
                ('Resultado del Ejercicio',             '',             '',     '',     '',         '',     1000.0, 0.0,    1000.0),
                ('Total',                               1190.0,         1190.0, 1190.0, 1190.0,     1190.0, 1190.0, 0.0,    1000.0),
            ],
        )

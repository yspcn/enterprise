# -*- coding: utf-8 -*-
from .common import TestMxExtendedEdiCommon
from odoo.tests import tagged
from odoo.exceptions import ValidationError

from freezegun import freeze_time


@tagged('post_install', '-at_install')
class TestExternalTrade(TestMxExtendedEdiCommon):

    def test_invoice_cfdi_external_trade(self):
        with freeze_time(self.frozen_today):
            product_b = self.env['product.product'].create({
                'name': 'product_mx_2',
                'weight': 2,
                'uom_po_id': self.env.ref('uom.product_uom_kgm').id,
                'uom_id': self.env.ref('uom.product_uom_kgm').id,
                'lst_price': 1000.0,
                'property_account_income_id': self.company_data['default_account_revenue'].id,
                'property_account_expense_id': self.company_data['default_account_expense'].id,
                'unspsc_code_id': self.env.ref('product_unspsc.unspsc_code_01010101').id,
            })

            self.invoice.write({
                'l10n_mx_edi_external_trade': True,
                'invoice_line_ids': [
                    (0, 0, {  # same as first line but different price and quantity and no discount, see TestMxExtendedEdiCommon.
                        'product_id': self.product.id,
                        'price_unit': 1000.0,
                        'quantity': 2,
                        'discount': 0,
                        'tax_ids': [(6, 0, (self.tax_16 + self.tax_10_negative).ids)], }),
                    (0, 0, {
                        'product_id': product_b.id,
                        'price_unit': 250.0,
                        'quantity': 2,
                        'discount': 10,
                        'tax_ids': [(6, 0, (self.tax_16 + self.tax_10_negative).ids)], }),
                ],
            })

            self.invoice._post()
            values = self.edi_format._l10n_mx_edi_get_invoice_cfdi_values(self.invoice)
            self.assertEqual(values['ext_trade_goods_details'][0]['line_total_usd'], 6000)  # 1000*2 + 2000*5 wo discount and converted to USD (/2)
            self.assertEqual(values['ext_trade_goods_details'][1]['line_total_usd'], 250)  # 250*2 wo discount and converted to USD (/2)
            self.assertEqual(values['ext_trade_total_price_subtotal_usd'], 6250)

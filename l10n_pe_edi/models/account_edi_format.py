# -*- coding: utf-8 -*-
# Part of Odoo. See LICENSE file for full copyright and licensing details.

import base64
import zipfile
import io
from requests.exceptions import ConnectionError, HTTPError, InvalidSchema, InvalidURL, ReadTimeout
from zeep.wsse.username import UsernameToken
from zeep import Client, Settings
from zeep.exceptions import Fault
from zeep.transports import Transport
from lxml import etree
from lxml.objectify import fromstring
from copy import deepcopy

from odoo import models, fields, api, _, _lt
from odoo.addons.iap.tools.iap_tools import iap_jsonrpc
from odoo.exceptions import AccessError
from odoo.tools import html_escape

DEFAULT_IAP_ENDPOINT = 'https://iap-pe-edi.odoo.com'
DEFAULT_IAP_TEST_ENDPOINT = 'https://l10n-pe-edi-proxy-demo.odoo.com'


class AccountEdiFormat(models.Model):
    _inherit = 'account.edi.format'

    # -------------------------------------------------------------------------
    # EDI: HELPERS
    # -------------------------------------------------------------------------

    @api.model
    def _l10n_pe_edi_zip_edi_document(self, documents):
        buffer = io.BytesIO()
        zipfile_obj = zipfile.ZipFile(buffer, 'w')
        for filename, content in documents:
            zipfile_obj.writestr(filename, content, compress_type=zipfile.ZIP_DEFLATED)
        zipfile_obj.close()
        content = buffer.getvalue()
        buffer.close()
        return content

    @api.model
    def _l10n_pe_edi_unzip_edi_document(self, zip_str):
        buffer = io.BytesIO(zip_str)
        zipfile_obj = zipfile.ZipFile(buffer)
        filename = zipfile_obj.namelist()[0]
        content = zipfile_obj.read(filename)
        buffer.close()
        return content

    @api.model
    def _l10n_pe_edi_get_general_error_messages(self):
        return {
            'L10NPE02': _lt("The zip file is corrupted, please check that the zip we are reading is the one you need."),
            'L10NPE03': _lt("The XML inside of the zip file is corrupted or has been altered, please review the XML "
                            "inside of the XML we are reading."),
            'L10NPE07': _lt("Trying to send the invoice to the OSE the webservice returned a controlled error, please "
                            "try again later, the error is on their side not here."),
            'L10NPE08': _lt("Check your firewall parameters, it is not being possible to connect with server to sign "
                            "invoices."),
            'L10NPE10': _lt("The URL provided to connect to the OSE is wrong, please check your implementation."),
            'L10NPE11': _lt("The XML generated is not valid."),
            'L10NPE16': _lt("There was an error while establishing the connection to the server, try again and "
                            "if it fails check the URL in the parameter l10n_pe_edi.endpoint."),
            'L10NPE17': _lt("There are problems with the connection to the IAP server. "
                            "Please try again in a few minutes."),
            'L10NPE18': _lt("The URL provided for the IAP server is wrong, please go to  Settings --> System "
                            "Parameters and add the right URL to parameter l10n_pe_edi.endpoint."),
        }

    @api.model
    def _l10n_pe_edi_get_cdr_error_messages(self):
        """The codes from the response of the CDR  and the service we are consulting must be processed to find if the
        message is common, if it is, we will set a friendly message giving instructions on how to fix the
        error/warning."""
        return {
            '2800': _lt("The type of identity document used for the client is not valid. Review the type of document "
                        "used in the client and change it according to the case of the document to be created. For "
                        "invoices it's only valid to use RUC as identity document."),
            '2801': _lt("The VAT you use for the customer is a DNI type, to be a valid DNI it must be the exact length "
                        "of 8 digits."),
            '2315': _lt("The cancellation reason field should not be empty when canceling the invoice, you must return "
                        "this invoice to Draft, edit the document and enter a cancellation reason."),
            '3105': _lt("One or more lines of this document do not have taxes assigned, to solve this you must return "
                        "the document to the Draft state and place taxes on the lines that do not have them."),
            '4332': _lt("One or more products do not have the UNSPSC code configured, to avoid this warning you must "
                        "configure a code for this product. This warning does not invalidate the document."),
            '2017': _lt("For invoices, the customer's identity document must be RUC. Check that the client has a valid "
                        "RUC and the type of document is RUC."),
            '3206': _lt("The type of operation is not valid for the type of document you are trying to create. The "
                        "document must return to Draft state and change the type of operation."),
            '2022': _lt("The name of the Partner must be changed to at least 2 characters to meet the proper standard."),
            '151': _lt("The name of the file depends on the sequence in the journal, please go to the journal and "
                       "configure the prefix as LLL- (three (3) letters plus a dash and the 3 letters must be UPPERCASE."),
            '156': _lt("The zip file is corrupted, check again if the file trying to access is not damaged."),
            '2119': _lt("The invoice related to this Credit Note has not been reported, go to the invoice related and "
                        "sign it in order to generate this Credit Note."),
            '2120': _lt("The invoice related to this Credit Note has been canceled, set this document to draft and "
                        "cancel it."),
            '2209': _lt("The invoice related to this Debit Not has not been reported, go to the invoice related and "
                        "sign it in order to generate this Debit Note"),
            '2207': _lt("The invoice related to this Debit Not has been canceled, set this document to draft and "
                        "cancel it."),
            '001': _lt("This invoice has been validated by the OSE and we can not allow set it to draft, please try "
                       "to revert it with a credit not or cancel it and create a new one instead."),
            '1033': _lt("This document already exists on the OSE side.  Check if you gave a proper unique name to your "
                        "document. "),
            '1034': _lt("Check that the VAT set in the company is correct, this error generally happen when you did "
                        "not set a proper VAT in the company, go to company form and set it properly.."),
            '2371': _lt("Check your tax configuration, go to Configuration -> Taxes and set the field "
                        "'Affectation reason' to set it by default or set the proper valie in the field Affect. Reason "
                        "in the line"),
            '2204': _lt("The document type of the invoice related is not the same of this document. Check the "
                        "document type of the invoice related and set this document with that document type. Incase of "
                        "this document being posted and having a number already, reset to draft and cancel it, this "
                        "document will be cancelled locally and not reported."),
            '2116': _lt("The document type of the invoice related is not the same of this document. Check the "
                        "document type of the invoice related and set this document with that document type. Incase of "
                        "this document being posted and having a number already, reset to draft and cancel it, this "
                        "document will be cancelled locally and not reported."),
        }

    @api.model
    def _l10n_pe_edi_decode_cdr(self, cdr_str):
        self.ensure_one()

        cdr_tree = etree.fromstring(cdr_str)
        code_element = cdr_tree.find('.//{*}Fault/{*}faultstring')
        message_element = cdr_tree.find('.//{*}message')
        if code_element is not None:
            code = code_element.text
            message = message_element.text
            error_messages_map = self._l10n_pe_edi_get_cdr_error_messages()
            error_message = '%s<br/><br/><b>%s</b><br/>%s|%s' % (
                error_messages_map.get(code, _("We got an error response from the OSE. ")),
                _('Original message:'),
                html_escape(code),
                html_escape(message),
            )
            return {'error': error_message}

        cdr_number_elements = cdr_tree.xpath('//ticket')
        if cdr_number_elements:
            return {'number': cdr_number_elements[0].text}

        return {}

    def _l10n_pe_edi_get_edi_values(self, invoice):
        self.ensure_one()

        def format_float(amount, precision=2):
            ''' Helper to format monetary amount as a string with 2 decimal places. '''
            if amount is None or amount is False:
                return None
            return '%.*f' % (precision, amount)

        def unit_amount(amount, quantity):
            ''' Helper to divide amount by quantity by taking care about float division by zero. '''
            if quantity:
                return invoice.currency_id.round(amount / quantity)
            else:
                return 0.0

        values = {
            'record': invoice,
            'invoice_lines_vals': [],
            'certificate_date': self.env['l10n_pe_edi.certificate']._get_pe_current_datetime().date(),
            'format_float': format_float,
            'tax_details': {
                'total_excluded': 0.0,
                'total_included': 0.0,
                'total_taxes': 0.0,
            },
        }
        tax_details = values['tax_details']

        # Invoice lines.
        tax_res_grouped = {}
        invoice_lines = invoice.invoice_line_ids.filtered(lambda line: not line.display_type)
        for i, line in enumerate(invoice_lines, start=1):
            price_unit_wo_discount = line.price_unit * (1.0 - (line.discount or 0.0) / 100.0)

            taxes_res = line.tax_ids.compute_all(
                price_unit_wo_discount,
                currency=line.currency_id,
                quantity=line.quantity,
                product=line.product_id,
                partner=line.partner_id,
                is_refund=invoice.move_type in ('out_refund', 'in_refund'),
            )

            taxes_res.update({
                'unit_total_included': unit_amount(taxes_res['total_included'], line.quantity),
                'unit_total_excluded': unit_amount(taxes_res['total_excluded'], line.quantity),
                'price_unit_type_code': '01' if not line.currency_id.is_zero(price_unit_wo_discount) else '02',
            })
            for tax_res in taxes_res['taxes']:
                tax = self.env['account.tax'].browse(tax_res['id'])
                tax_res.update({
                    'tax_amount': tax.amount,
                    'tax_amount_type': tax.amount_type,
                    'price_unit_type_code': '01' if not line.currency_id.is_zero(tax_res['amount']) else '02',
                    'l10n_pe_edi_tax_code': tax.l10n_pe_edi_tax_code,
                    'l10n_pe_edi_group_code': tax.tax_group_id.l10n_pe_edi_code,
                    'l10n_pe_edi_international_code': tax.l10n_pe_edi_international_code,
                })

                tuple_key = (
                    tax_res['l10n_pe_edi_group_code'],
                    tax_res['l10n_pe_edi_international_code'],
                    tax_res['l10n_pe_edi_tax_code'],
                )

                tax_res_grouped.setdefault(tuple_key, {
                    'base': 0.0,
                    'amount': 0.0,
                    'l10n_pe_edi_group_code': tax_res['l10n_pe_edi_group_code'],
                    'l10n_pe_edi_international_code': tax_res['l10n_pe_edi_international_code'],
                    'l10n_pe_edi_tax_code': tax_res['l10n_pe_edi_tax_code'],
                })
                tax_res_grouped[tuple_key]['base'] += tax_res['base']
                tax_res_grouped[tuple_key]['amount'] += tax_res['amount']

                tax_details['total_excluded'] += tax_res['base']
                tax_details['total_included'] += tax_res['base'] + tax_res['amount']
                tax_details['total_taxes'] += tax_res['amount']

                values['invoice_lines_vals'].append({
                    'index': i,
                    'line': line,
                    'tax_details': taxes_res,
                })

        values['tax_details']['grouped_taxes'] = list(tax_res_grouped.values())

        return values

    # -------------------------------------------------------------------------
    # EDI: IAP service
    # -------------------------------------------------------------------------

    def _l10n_pe_edi_get_iap_buy_credits_message(self, company):
        base_url = 'https://iap-sandbox.odoo.com/iap/1/credit' if company.l10n_pe_edi_test_env else ''
        url = self.env['iap.account'].get_credits_url(service_name="l10n_pe_edi", base_url=base_url)
        return '''<p><b>%s</b></p><p>%s</p>''' % (
            _('You have insufficient credits to sign or verify this document!'),
            _('Please proceed to buy more credits <a href="%s">here.</a>', html_escape(url)),
        )

    def _l10n_pe_edi_get_iap_params(self, company):
        ir_params = self.env['ir.config_parameter'].sudo()
        if company.l10n_pe_edi_test_env:
            default_endpoint = DEFAULT_IAP_TEST_ENDPOINT
        else:
            default_endpoint = DEFAULT_IAP_ENDPOINT
        iap_server_url = ir_params.get_param('l10n_pe_edi.endpoint', default_endpoint)
        iap_token = self.env['iap.account'].get('l10n_pe_edi').account_token
        dbuuid = ir_params.get_param('database.uuid')
        return dbuuid, iap_server_url, iap_token

    def _l10n_pe_edi_sign_invoices_iap(self, invoice, edi_filename, edi_str):
        self.ensure_one()
        edi_tree = fromstring(edi_str)

        # Dummy Signature to allow check the XSD, this will be replaced on IAP.
        namespaces = {'ds': 'http://www.w3.org/2000/09/xmldsig#'}
        edi_tree_copy = deepcopy(edi_tree)
        signature_element = edi_tree_copy.xpath('.//ds:Signature', namespaces=namespaces)[0]
        signature_str = self.env.ref('l10n_pe_edi.pe_ubl_2_1_signature')._render({'digest_value': ''})
        signature_element.getparent().replace(signature_element, fromstring(signature_str))

        error = self.env['ir.attachment']._l10n_pe_edi_check_with_xsd(edi_tree_copy, invoice.l10n_latam_document_type_id.code)
        if error:
            return {'error': "<b>%s</b><br/>%s" % (_('XSD validation failed:'), html_escape(error)), 'blocking_level': 'error'}

        dbuuid, iap_server_url, iap_token = self._l10n_pe_edi_get_iap_params(invoice.company_id)

        rpc_params = {
            'vat': invoice.company_id.vat,
            'doc_type': invoice.l10n_latam_document_type_id.code,
            'dbuuid': dbuuid,
            'fname': edi_filename,
            'xml': base64.b64encode(edi_str).decode('utf-8'),
            'token': iap_token,
        }

        try:
            result = iap_jsonrpc(iap_server_url + '/iap/l10n_pe_edi/1/send_bill', params=rpc_params, timeout=1500)
        except InvalidSchema:
            return {'error': self._l10n_pe_edi_get_general_error_messages()['L10NPE16'], 'blocking_level': 'error'}
        except AccessError:
            return {'error': self._l10n_pe_edi_get_general_error_messages()['L10NPE17'], 'blocking_level': 'warning'}
        except InvalidURL:
            return {'error': self._l10n_pe_edi_get_general_error_messages()['L10NPE18'], 'blocking_level': 'error'}

        if result.get('message'):
            if result['message'] == 'no-credit':
                error_message = self._l10n_pe_edi_get_iap_buy_credits_message(invoice.company_id)
            else:
                error_message = result['message']
            return {'error': error_message, 'blocking_level': 'error'}

        cdr_str = result.get('cdr') and base64.b64decode(result['cdr'])
        cdr_decoded = self._l10n_pe_edi_decode_cdr(cdr_str) if cdr_str else {}

        if cdr_decoded.get('error'):
            return {'error': cdr_decoded['error'], 'blocking_level': 'error'}

        xml_document = result.get('signed') and self._l10n_pe_edi_unzip_edi_document(base64.b64decode(result['signed']))
        return {'xml_document': xml_document, 'cdr': cdr_str}

    def _l10n_pe_edi_cancel_invoices_step_1_iap(self, company, invoices, void_filename, void_str):
        self.ensure_one()
        dbuuid, iap_server_url, iap_token = self._l10n_pe_edi_get_iap_params(company)

        rpc_params = {
            'vat': company.vat,
            'dbuuid': dbuuid,
            'fname': void_filename,
            'xml': base64.encodebytes(void_str).decode('utf-8'),
            'token': iap_token,
        }

        try:
            result = iap_jsonrpc(iap_server_url + '/iap/l10n_pe_edi/1/send_summary', params=rpc_params, timeout=15)
        except Fault:
            return {'error': self._l10n_pe_edi_get_general_error_messages()['L10NPE07'], 'blocking_level': 'warning'}
        except AccessError:
            return {'error': self._l10n_pe_edi_get_general_error_messages()['L10NPE17'], 'blocking_level': 'warning'}
        except KeyError:
            return {'error': self._l10n_pe_edi_get_general_error_messages()['L10NPE18'], 'blocking_level': 'error'}

        if result.get('message'):
            if result['message'] == 'no-credit':
                error_message = self._l10n_pe_edi_get_iap_buy_credits_message(company)
            else:
                error_message = result['message']
            return {'error': error_message, 'blocking_level': 'error'}

        cdr_str = result.get('cdr') and base64.b64decode(result['cdr'])
        cdr_decoded = self._l10n_pe_edi_decode_cdr(cdr_str) if cdr_str else {}
        cdr_number = cdr_decoded.get('number')

        if cdr_decoded.get('error'):
            return {'error': cdr_decoded['error'], 'blocking_level': 'error'}

        xml_document = result.get('signed') and self._l10n_pe_edi_unzip_edi_document(base64.b64decode(result['signed']))
        return {'xml_document': xml_document, 'cdr': cdr_str, 'cdr_number': cdr_number}

    def _l10n_pe_edi_cancel_invoices_step_2_iap(self, company, edi_values, cdr_number):
        self.ensure_one()
        dbuuid, iap_server_url, iap_token = self._l10n_pe_edi_get_iap_params(company)

        rpc_params = {
            'vat': company.vat,
            'dbuuid': dbuuid,
            'number': cdr_number,
            'token': iap_token,
        }

        try:
            result = iap_jsonrpc(iap_server_url + '/iap/l10n_pe_edi/1/get_status', params=rpc_params, timeout=15)
        except Fault:
            return {'error': self._l10n_pe_edi_get_general_error_messages()['L10NPE07'], 'blocking_level': 'warning'}
        except KeyError:
            return {'error': self._l10n_pe_edi_get_general_error_messages()['L10NPE18'], 'blocking_level': 'error'}
        except AccessError:
            return {'error': self._l10n_pe_edi_get_general_error_messages()['L10NPE17'], 'blocking_level': 'warning'}

        if result.get('message'):
            if result['message'] == 'no-credit':
                error_message = self._l10n_pe_edi_get_iap_buy_credits_message(company)
            else:
                error_message = result['message']
            return {'error': error_message, 'blocking_level': 'error'}

        cdr_str = result.get('cdr') and base64.b64decode(result['cdr'])
        cdr_decoded = self._l10n_pe_edi_decode_cdr(cdr_str) if cdr_str else {}

        if cdr_decoded.get('error'):
            return {'error': cdr_decoded['error'], 'blocking_level': 'error'}

        return {'success': True, 'cdr': cdr_str}

    # -------------------------------------------------------------------------
    # EDI: SUNAT / DIGIFLOW services
    # -------------------------------------------------------------------------

    def _l10n_pe_edi_get_digiflow_credentials(self, company):
        self.ensure_one()
        res = {'fault_ns': 's'}
        if company.l10n_pe_edi_test_env:
            res.update({
                'wsdl': 'https://ose-test.com/ol-ti-itcpe/',
                'token': UsernameToken('20557912879MODDATOS', 'moddatos'),
            })
        else:
            res.update({
                'wsdl': 'https://ose.pe/ol-ti-itcpe/billService',
                'token': UsernameToken(company.l10n_pe_edi_provider_username, company.l10n_pe_edi_provider_password),
            })
        return res

    def _l10n_pe_edi_get_sunat_credentials(self, company):
        self.ensure_one()
        res = {'fault_ns': 'soap-env'}
        if company.l10n_pe_edi_test_env:
            res.update({
                'wsdl': 'https://e-beta.sunat.gob.pe/ol-ti-itcpfegem-beta/billService?wsdl',
                'token': UsernameToken('MODDATOS', 'MODDATOS'),
            })
        else:
            res.update({
                'wsdl': 'https://e-factura.sunat.gob.pe/ol-ti-itcpfegem/billService?wsdl',
                'token': UsernameToken(company.l10n_pe_edi_provider_username, company.l10n_pe_edi_provider_password),
            })
        return res

    def _l10n_pe_edi_sign_invoices_sunat_digiflow_common(self, invoice, edi_filename, edi_str, credentials):
        self.ensure_one()

        if not invoice.company_id.l10n_pe_edi_certificate_id:
            return {'error': _("No valid certificate found for %s company.", invoice.company_id.display_name)}

        # Sign the document.
        edi_tree = fromstring(edi_str)
        edi_tree = invoice.company_id.l10n_pe_edi_certificate_id.sudo()._sign(edi_tree)
        error = self.env['ir.attachment']._l10n_pe_edi_check_with_xsd(edi_tree, invoice.l10n_latam_document_type_id.code)
        if error:
            return {'error': _('XSD validation failed: %s', error), 'blocking_level': 'error'}
        edi_str = etree.tostring(edi_tree, xml_declaration=True, encoding='ISO-8859-1')

        zip_edi_str = self._l10n_pe_edi_zip_edi_document([('%s.xml' % edi_filename, edi_str)])
        transport = Transport(operation_timeout=15, timeout=15)
        try:
            settings = Settings(raw_response=True)
            client = Client(
                wsdl=credentials['wsdl'],
                wsse=credentials['token'],
                settings=settings,
                transport=transport,
            )
            result = client.service.sendBill('%s.zip' % edi_filename, zip_edi_str)
            result.raise_for_status()
        except Fault:
            return {'error': self._l10n_pe_edi_get_general_error_messages()['L10NPE07'], 'blocking_level': 'warning'}
        except ConnectionError:
            return {'error': self._l10n_pe_edi_get_general_error_messages()['L10NPE08'], 'blocking_level': 'warning'}
        except HTTPError:
            return {'error': self._l10n_pe_edi_get_general_error_messages()['L10NPE10'], 'blocking_level': 'warning'}
        except TypeError:
            return {'error': self._l10n_pe_edi_get_general_error_messages()['L10NPE11'], 'blocking_level': 'error'}
        except ReadTimeout:
            return {'error': self._l10n_pe_edi_get_general_error_messages()['L10NPE12'], 'blocking_level': 'warning'}
        cdr_str = result.content
        cdr_decoded = self._l10n_pe_edi_decode_cdr(cdr_str)

        if cdr_decoded.get('error'):
            return {'error': cdr_decoded['error'], 'blocking_level': 'error'}

        return {'xml_document': edi_str, 'cdr': cdr_str}

    def _l10n_pe_edi_sign_invoices_sunat(self, invoice, edi_filename, edi_str):
        credentials = self._l10n_pe_edi_get_sunat_credentials(invoice.company_id)
        return self._l10n_pe_edi_sign_invoices_sunat_digiflow_common(invoice, edi_filename, edi_str, credentials)

    def _l10n_pe_edi_sign_invoices_digiflow(self, invoice, edi_filename, edi_str):
        credentials = self._l10n_pe_edi_get_digiflow_credentials(invoice.company_id)
        return self._l10n_pe_edi_sign_invoices_sunat_digiflow_common(invoice, edi_filename, edi_str, credentials)

    def _l10n_pe_edi_cancel_invoices_step_1_sunat_digiflow_common(self, company, invoices, void_filename, void_str, credentials):
        self.ensure_one()

        void_tree = fromstring(void_str)
        void_tree = company.l10n_pe_edi_certificate_id.sudo()._sign(void_tree)
        void_str = etree.tostring(void_tree, xml_declaration=True, encoding='ISO-8859-1')
        zip_void_str = self._l10n_pe_edi_zip_edi_document([('%s.xml' % void_filename, void_str)])
        transport = Transport(operation_timeout=15, timeout=15)

        try:
            settings = Settings(raw_response=True)
            client = Client(
                wsdl=credentials['wsdl'],
                wsse=credentials['token'],
                settings=settings,
                transport=transport,
            )
            result = client.service.sendSummary('%s.zip' % void_filename,  zip_void_str)
            result.raise_for_status()
        except Fault:
            return {'error': self._l10n_pe_edi_get_general_error_messages()['L10NPE07'], 'blocking_level': 'warning'}
        except (InvalidSchema, KeyError):
            return {'error': self._l10n_pe_edi_get_general_error_messages()['L10NPE08'], 'blocking_level': 'error'}

        cdr_str = result.content
        cdr_decoded = self._l10n_pe_edi_decode_cdr(cdr_str)
        cdr_number = cdr_decoded.get('number')

        if cdr_decoded.get('error'):
            return {'error': cdr_decoded['error'], 'blocking_level': 'error'}

        return {'xml_document': void_str, 'cdr': cdr_str, 'cdr_number': cdr_number}

    def _l10n_pe_edi_cancel_invoices_step_1_sunat(self, company, invoices, void_filename, void_str):
        credentials = self._l10n_pe_edi_get_sunat_credentials(company)
        return self._l10n_pe_edi_cancel_invoices_step_1_sunat_digiflow_common(company, invoices, void_filename, void_str, credentials)

    def _l10n_pe_edi_cancel_invoices_step_1_digiflow(self, company, invoices, void_filename, void_str):
        credentials = self._l10n_pe_edi_get_digiflow_credentials(company)
        return self._l10n_pe_edi_cancel_invoices_step_1_sunat_digiflow_common(company, invoices, void_filename, void_str, credentials)

    def _l10n_pe_edi_cancel_invoices_step_2_sunat_digiflow_common(self, company, edi_values, cdr_number, credentials):
        self.ensure_one()

        transport = Transport(operation_timeout=15, timeout=15)

        try:
            settings = Settings(raw_response=True)
            client = Client(
                wsdl=credentials['wsdl'],
                wsse=credentials['token'],
                settings=settings,
                transport=transport,
            )
            result = client.service.getStatus(cdr_number)
            result.raise_for_status()
        except Fault:
            return {'error': self._l10n_pe_edi_get_general_error_messages()['L10NPE07'], 'blocking_level': 'warning'}
        except (InvalidSchema, KeyError):
            return {'error': self._l10n_pe_edi_get_general_error_messages()['L10NPE08'], 'blocking_level': 'error'}

        cdr_str = result.content
        cdr_decoded = self._l10n_pe_edi_decode_cdr(cdr_str)

        if cdr_decoded.get('error'):
            return {'error': cdr_decoded['error'], 'blocking_level': 'error'}

        return {'success': True, 'cdr': cdr_str}

    def _l10n_pe_edi_cancel_invoices_step_2_sunat(self, company, edi_values, cdr_number):
        credentials = self._l10n_pe_edi_get_sunat_credentials(company)
        return self._l10n_pe_edi_cancel_invoices_step_2_sunat_digiflow_common(company, edi_values, cdr_number, credentials)

    def _l10n_pe_edi_cancel_invoices_step_2_digiflow(self, company, edi_values, cdr_number):
        credentials = self._l10n_pe_edi_get_digiflow_credentials(company)
        return self._l10n_pe_edi_cancel_invoices_step_2_sunat_digiflow_common(company, edi_values, cdr_number, credentials)

    # -------------------------------------------------------------------------
    # EDI OVERRIDDEN METHODS
    # -------------------------------------------------------------------------

    def _is_required_for_invoice(self, invoice):
        # OVERRIDE
        self.ensure_one()
        if self.code != 'pe_ubl_2_1':
            return super()._is_required_for_invoice(invoice)

        return invoice.l10n_pe_edi_is_required

    def _needs_web_services(self):
        # OVERRIDE
        return self.code == 'pe_ubl_2_1' or super()._needs_web_services()

    def _support_batching(self, move=None, state=None, company=None):
        # OVERRIDE
        if self.code == 'pe_ubl_2_1':
            return state == 'to_cancel' and move.is_invoice()

        return super()._support_batching(move=move, state=state, company=company)

    def _get_batch_key(self, move, state):
        # OVERRIDE
        # Handle the 2 steps cancel by creating an indirection in jobs.
        if self.code == 'pe_ubl_2_1' and state == 'to_cancel':
            return (move.l10n_pe_edi_cancel_cdr_number,)
        return super()._get_batch_key(move, state)

    def _check_move_configuration(self, move):
        # OVERRIDE
        res = super()._check_move_configuration(move)
        if self.code != 'pe_ubl_2_1':
            return res

        if not move.company_id.vat:
            res.append(_("VAT number is missing on company %s") % move.company_id.display_name)
        lines = move.invoice_line_ids.filtered(lambda line: not line.display_type)
        for line in lines:
            taxes = line.tax_ids
            if len(taxes) > 1 and len(taxes.filtered(lambda t: t.tax_group_id.l10n_pe_edi_code == 'IGV')) > 1:
                res.append(_("You can't have more than one IGV tax per line to generate a legal invoice in Peru"))
        if any(not line.tax_ids for line in move.invoice_line_ids if not line.display_type):
            res.append(_("Taxes need to be assigned on all invoice lines"))

        return res

    def _is_compatible_with_journal(self, journal):
        # OVERRIDE
        if self.code != 'pe_ubl_2_1':
            return super()._is_compatible_with_journal(journal)
        return journal.type == 'sale' and journal.country_code == 'PE' and journal.l10n_latam_use_documents

    def _post_invoice_edi(self, invoices, test_mode=False):
        # OVERRIDE
        if self.code != 'pe_ubl_2_1':
            return super()._post_invoice_edi(invoices, test_mode=test_mode)

        template_by_latam_type_mapping = {
            '07': 'pe_ubl_2_1_credit_note',
            '08': 'pe_ubl_2_1_debit_note',
            '01': 'pe_ubl_2_1_invoice',
            '03': 'pe_ubl_2_1_invoice',
        }

        invoice = invoices # Batching is disabled for this EDI.
        provider = invoice.company_id.l10n_pe_edi_provider

        edi_filename = '%s-%s-%s' % (
            invoice.company_id.vat,
            invoice.l10n_latam_document_type_id.code,
            invoice.name.replace(' ', ''),
        )
        latam_invoice_type = template_by_latam_type_mapping.get(invoice.l10n_latam_document_type_id.code)

        if not latam_invoice_type:
            return {invoice: {'error': _("Missing LATAM document code.")}}

        edi_values = self._l10n_pe_edi_get_edi_values(invoice)
        edi_str = self.env.ref('l10n_pe_edi.%s' % latam_invoice_type)._render(edi_values)

        # test_mode indicates this method is called from odoo tests in order to check the xml values. In that case,
        # simulate the EDI always success.
        if test_mode:
            res = {}
            for invoice in invoices:
                zip_edi_str = self._l10n_pe_edi_zip_edi_document([('%s.xml' % edi_filename, edi_str)])
                res[invoice] = {'attachment': self.env['ir.attachment'].create({
                    'res_model': invoice._name,
                    'res_id': invoice.id,
                    'type': 'binary',
                    'name': '%s.zip' % edi_filename,
                    'datas': base64.encodebytes(zip_edi_str),
                    'mimetype': 'application/zip',
                })}
            return res

        res = getattr(self, '_l10n_pe_edi_sign_invoices_%s' % provider)(invoice, edi_filename, edi_str)

        if res.get('error'):
            return {invoice: res}

        # Chatter.
        documents = []
        if res.get('xml_document'):
            documents.append(('%s.xml' % edi_filename, res['xml_document']))
        if res.get('cdr'):
            documents.append(('CDR-%s.xml' % edi_filename, res['cdr']))
        if documents:
            zip_edi_str = self._l10n_pe_edi_zip_edi_document(documents)
            res['attachment'] = self.env['ir.attachment'].create({
                'res_model': invoice._name,
                'res_id': invoice.id,
                'type': 'binary',
                'name': '%s.zip' % edi_filename,
                'datas': base64.encodebytes(zip_edi_str),
                'mimetype': 'application/zip',
            })
            message = _("The EDI document was successfully created and signed by the government.")
            invoice.with_context(no_new_invoice=True).message_post(
                body=message,
                attachment_ids=res['attachment'].ids,
            )

        return {invoice: res}

    def _l10n_pe_edi_cancel_invoice_edi_step_1(self, invoices):
        self.ensure_one()
        certificate_date = self.env['l10n_pe_edi.certificate']._get_pe_current_datetime().date()
        company = invoices[0].company_id # documents are always batched by company in account_edi.
        provider = company.l10n_pe_edi_provider

        # Prepare the void documents to void all invoices at once.
        void_number = self.env['ir.sequence'].next_by_code('l10n_pe_edi.summary.sequence')
        void_values = {
            'certificate_date': certificate_date,
            'void_number': void_number,
            'company': company,
            'records': invoices,
        }
        void_str = self.env.ref('l10n_pe_edi.pe_ubl_2_1_void_documents')._render(void_values)
        void_filename = '%s-%s' % (company.vat, void_number)

        res = getattr(self, '_l10n_pe_edi_cancel_invoices_step_1_%s' % provider)(company, invoices, void_filename, void_str)

        if res.get('error'):
            return {invoice: res for invoice in invoices}

        if not res.get('cdr_number'):
            error = _("The EDI document failed to be cancelled because the cancellation CDR number is missing.")
            return {invoice: {'error': error} for invoice in invoices}

        # Chatter.
        message = _("Cancellation is in progress in the government side (CDR number: %s).", html_escape(res['cdr_number']))
        if res.get('xml_document'):
            void_attachment = self.env['ir.attachment'].create({
                'type': 'binary',
                'name': '%s.zip' % void_filename,
                'datas': base64.encodebytes(res['xml_document']),
                'mimetype': 'application/zip',
            })
            for invoice in invoices:
                invoice.with_context(no_new_invoice=True).message_post(
                    body=message,
                    attachment_ids=void_attachment.ids,
                )

        invoices.write({'l10n_pe_edi_cancel_cdr_number': res['cdr_number']})
        return {invoice: {'error': message, 'blocking_level': 'info'} for invoice in invoices}

    def _l10n_pe_edi_cancel_invoice_edi_step_2(self, invoices, edi_attachments, cdr_number):
        self.ensure_one()
        company = invoices[0].company_id # documents are always batched by company in account_edi.
        provider = company.l10n_pe_edi_provider
        edi_values = list(zip(invoices, edi_attachments))

        res = getattr(self, '_l10n_pe_edi_cancel_invoices_step_2_%s' % provider)(company, edi_values, cdr_number)

        if res.get('error'):
            return {invoice: res for invoice in invoices}
        if not res.get('success'):
            error = _("The EDI document failed to be cancelled for unknown reason.")
            return {invoice: {'error': error} for invoice in invoices}

        # Chatter.
        message = _("The EDI document was successfully cancelled by the government (CDR number: %s).", html_escape(cdr_number))
        for invoice, attachment in edi_values:
            cdr_void_attachment = self.env['ir.attachment'].create({
                'res_model': invoice._name,
                'res_id': invoice.id,
                'type': 'binary',
                'name': 'CDR-VOID-%s.xml' % attachment.name[:-4],
                'datas': base64.encodebytes(res['cdr']),
                'mimetype': 'application/xml',
            })
            invoice.with_context(no_new_invoice=True).message_post(
                body=message,
                attachment_ids=cdr_void_attachment.ids,
            )
        invoices.write({'l10n_pe_edi_cancel_cdr_number': False})
        return {invoice: {'success': True} for invoice in invoices}

    def _cancel_invoice_edi(self, invoices, test_mode=False):
        # OVERRIDE
        if self.code != 'pe_ubl_2_1':
            return super()._cancel_invoice_edi(invoices, test_mode=test_mode)

        company = invoices[0].company_id # documents are always batched by company in account_edi.
        edi_attachments = self.env['ir.attachment']
        res = {}
        for invoice in invoices:

            if not invoice.l10n_pe_edi_cancel_reason:
                res[invoice] = {'error': _("Please put a cancel reason")}
                continue

            if test_mode:
                res[invoice] = {'success': True}
                continue

            edi_attachments |= invoice._get_edi_attachment(self)

        res = {}
        invoices_with_cdr = invoices.filtered('l10n_pe_edi_cancel_cdr_number')
        if invoices_with_cdr:
            # Cancel part 2.
            # Ensure the whole batch of invoices sharing the same number is there. Return an error if it's not the case
            # because the whole batch must be processed at once and locked in order to avoid asynchronous errors.
            cdr_number = invoices_with_cdr[0].l10n_pe_edi_cancel_cdr_number
            invoices_same_number = invoices_with_cdr.filtered(lambda move: move.l10n_pe_edi_cancel_cdr_number == cdr_number)
            all_invoices_same_number = self.env['account.move'].search([
                ('l10n_pe_edi_cancel_cdr_number', '=', cdr_number),
                ('company_id', '=', company.id),
            ])
            if len(invoices_same_number) == len(all_invoices_same_number):
                # Process.
                edi_attachments = self.env['ir.attachment']
                for invoice in invoices_same_number:
                    edi_attachments |= invoice._get_edi_attachment(self)
                res.update(self._l10n_pe_edi_cancel_invoice_edi_step_2(invoices_same_number, edi_attachments, cdr_number))
            else:
                # Error.
                for invoice in invoices_same_number:
                    res[invoice] = {'error': _("All invoices sharing the same CDR number (%s) must be processed at once", html_escape(cdr_number))}
        else:
            # Cancel part 1.
            res.update(self._l10n_pe_edi_cancel_invoice_edi_step_1(invoices))

        return res

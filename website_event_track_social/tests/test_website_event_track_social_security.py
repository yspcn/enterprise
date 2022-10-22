# -*- coding: utf-8 -*-
# Part of Odoo. See LICENSE file for full copyright and licensing details.

from dateutil.relativedelta import relativedelta

from odoo import fields
from odoo.addons.website_event_track.tests.common import TestEventTrackOnlineCommon
from odoo.tests.common import users


class TestTrackPushSecurity(TestEventTrackOnlineCommon):

    @classmethod
    def setUpClass(cls):
        super(TestTrackPushSecurity, cls).setUpClass()
        cls.website = cls.env['website'].create({'name': 'Website'})
        cls.event_0.write({'website_id': cls.website.id})

    @users('user_eventmanager')
    def test_track_social_security(self):
        track_1 = self.env['event.track'].create({
            'name': 'Track',
            'event_id': self.event_0.id,
            'date': fields.Datetime.now() + relativedelta(hours=2),
            'push_reminder': True,
            'push_reminder_delay': 10,
        })

        # event manager should be able to create push notifications even without social groups by
        # enabling the 'push_reminder' field
        push_reminder = self.env['social.post'].sudo().search([('event_track_id', '=', track_1.id)])
        self.assertEqual(1, len(push_reminder))
        self.assertEqual(
            "Your wishlisted track 'Track' will start in 10 minutes!",
            push_reminder.message)

        # event modifications should be correctly reflected to the push notification even without
        # social groups
        track_1.write({'name': 'New Name'})
        track_1.flush(['name', 'date'])
        push_reminder = self.env['social.post'].sudo().search([('event_track_id', '=', track_1.id)])
        self.assertEqual(
            "Your wishlisted track 'New Name' will start in 10 minutes!",
            push_reminder.message)

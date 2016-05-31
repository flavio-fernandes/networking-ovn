#
#    Licensed under the Apache License, Version 2.0 (the "License"); you may
#    not use this file except in compliance with the License. You may obtain
#    a copy of the License at
#
#         http://www.apache.org/licenses/LICENSE-2.0
#
#    Unless required by applicable law or agreed to in writing, software
#    distributed under the License is distributed on an "AS IS" BASIS, WITHOUT
#    WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied. See the
#    License for the specific language governing permissions and limitations
#    under the License.
#

import copy
import mock
import six

from neutron_lib import constants as const

from neutron.common import constants as n_const

from networking_ovn.common import acl as ovn_acl
from networking_ovn.common import constants as ovn_const
from networking_ovn.ovsdb import commands as cmd
from networking_ovn.tests import base
from networking_ovn.tests.unit import fakes


class TestACLs(base.TestCase):

    def setUp(self):
        super(TestACLs, self).setUp()
        self.driver = mock.Mock()
        self.driver._ovn = fakes.FakeOvsdbOvnIdl()
        self.fake_port = {'id': 'fake_port_id1',
                          'network_id': 'network_id1',
                          'fixed_ips': [{'subnet_id': 'subnet_id1',
                                         'ip_address': '1.1.1.1'}]}
        self.fake_subnet = {'id': 'subnet_id1',
                            'ip_version': 4,
                            'cidr': '1.1.1.0/24'}
        patcher = mock.patch(
            'neutron.agent.ovsdb.native.idlutils.row_by_value',
            lambda *args, **kwargs: mock.MagicMock())
        patcher.start()

    def test_drop_all_ip_traffic_for_port(self):
        acls = ovn_acl.drop_all_ip_traffic_for_port(self.fake_port)
        acl_to_lport = {'action': 'drop', 'direction': 'to-lport',
                        'external_ids': {'neutron:lport':
                                         self.fake_port['id']},
                        'log': False, 'lport': self.fake_port['id'],
                        'lswitch': 'neutron-network_id1',
                        'match': 'outport == "fake_port_id1" && ip',
                        'priority': 1001}
        acl_from_lport = {'action': 'drop', 'direction': 'from-lport',
                          'external_ids': {'neutron:lport':
                                           self.fake_port['id']},
                          'log': False, 'lport': self.fake_port['id'],
                          'lswitch': 'neutron-network_id1',
                          'match': 'inport == "fake_port_id1" && ip',
                          'priority': 1001}
        for acl in acls:
            if 'to-lport' in acl.values():
                self.assertEqual(acl_to_lport, acl)
            if 'from-lport' in acl.values():
                self.assertEqual(acl_from_lport, acl)

    def test_add_acl_dhcp(self):
        acls = ovn_acl.add_acl_dhcp(self.fake_port, self.fake_subnet)

        expected_match_to_lport = (
            'outport == "%s" && ip4 && ip4.src == %s && udp && udp.src == 67 '
            '&& udp.dst == 68') % (self.fake_port['id'],
                                   self.fake_subnet['cidr'])
        acl_to_lport = {'action': 'allow', 'direction': 'to-lport',
                        'external_ids': {'neutron:lport': 'fake_port_id1'},
                        'log': False, 'lport': 'fake_port_id1',
                        'lswitch': 'neutron-network_id1',
                        'match': expected_match_to_lport, 'priority': 1002}
        expected_match_from_lport = (
            'inport == "%s" && ip4 && '
            '(ip4.dst == 255.255.255.255 || ip4.dst == %s) && '
            'udp && udp.src == 68 && udp.dst == 67'
        ) % (self.fake_port['id'], self.fake_subnet['cidr'])
        acl_from_lport = {'action': 'allow', 'direction': 'from-lport',
                          'external_ids': {'neutron:lport': 'fake_port_id1'},
                          'log': False, 'lport': 'fake_port_id1',
                          'lswitch': 'neutron-network_id1',
                          'match': expected_match_from_lport, 'priority': 1002}
        for acl in acls:
            if 'to-lport' in acl.values():
                self.assertEqual(acl_to_lport, acl)
            if 'from-lport' in acl.values():
                self.assertEqual(acl_from_lport, acl)

    def _test_add_sg_rule_acl_for_port(self, sg_rule, direction, match):
        port = {'id': 'port-id',
                'network_id': 'network-id'}
        acl = ovn_acl.add_sg_rule_acl_for_port(port, sg_rule, match)
        self.assertEqual(acl, {'lswitch': 'neutron-network-id',
                               'lport': 'port-id',
                               'priority': ovn_const.ACL_PRIORITY_ALLOW,
                               'action': ovn_const.ACL_ACTION_ALLOW_RELATED,
                               'log': False,
                               'direction': direction,
                               'match': match,
                               'external_ids': {'neutron:lport': 'port-id'}})

    def test_add_sg_rule_acl_for_port_remote_ip_prefix(self):
        sg_rule = {'direction': 'ingress',
                   'ethertype': 'IPv4',
                   'remote_group_id': None,
                   'remote_ip_prefix': '1.1.1.0/24',
                   'protocol': None}
        match = 'outport == "port-id" && ip4 && ip4.src == 1.1.1.0/24'
        self._test_add_sg_rule_acl_for_port(sg_rule,
                                            'to-lport',
                                            match)
        sg_rule['direction'] = 'egress'
        match = 'inport == "port-id" && ip4 && ip4.dst == 1.1.1.0/24'
        self._test_add_sg_rule_acl_for_port(sg_rule,
                                            'from-lport',
                                            match)

    def test_add_sg_rule_acl_for_port_remote_group(self):
        sg_rule = {'direction': 'ingress',
                   'ethertype': 'IPv4',
                   'remote_group_id': 'sg1',
                   'remote_ip_prefix': None,
                   'protocol': None}
        match = 'outport == "port-id" && ip4 && (ip4.src == 1.1.1.100' \
                ' || ip4.src == 1.1.1.101' \
                ' || ip4.src == 1.1.1.102)'

        self._test_add_sg_rule_acl_for_port(sg_rule,
                                            'to-lport',
                                            match)
        sg_rule['direction'] = 'egress'
        match = 'inport == "port-id" && ip4 && (ip4.dst == 1.1.1.100' \
                ' || ip4.dst == 1.1.1.101' \
                ' || ip4.dst == 1.1.1.102)'
        self._test_add_sg_rule_acl_for_port(sg_rule,
                                            'from-lport',
                                            match)

    def test__update_acls_compute_difference(self):
        lswitch_name = 'lswitch-1'
        port1 = {'id': 'port-id1',
                 'network_id': lswitch_name,
                 'fixed_ips': [{'subnet_id': 'subnet-id',
                                'ip_address': '1.1.1.101'},
                               {'subnet_id': 'subnet-id-v6',
                                'ip_address': '2001:0db8::1:0:0:1'}]}
        port2 = {'id': 'port-id2',
                 'network_id': lswitch_name,
                 'fixed_ips': [{'subnet_id': 'subnet-id',
                                'ip_address': '1.1.1.102'},
                               {'subnet_id': 'subnet-id-v6',
                                'ip_address': '2001:0db8::1:0:0:2'}]}
        ports = [port1, port2]
        # OLD ACLs, allow IPv4 communication
        aclport1_old1 = {'priority': 1002, 'direction': 'from-lport',
                         'lport': port1['id'], 'lswitch': lswitch_name,
                         'match': 'inport == %s && ip4 && (ip.src == %s)' %
                         (port1['id'], port1['fixed_ips'][0]['ip_address'])}
        aclport1_old2 = {'priority': 1002, 'direction': 'from-lport',
                         'lport': port1['id'], 'lswitch': lswitch_name,
                         'match': 'inport == %s && ip6 && (ip.src == %s)' %
                         (port1['id'], port1['fixed_ips'][1]['ip_address'])}
        aclport1_old3 = {'priority': 1002, 'direction': 'to-lport',
                         'lport': port1['id'], 'lswitch': lswitch_name,
                         'match': 'ip4 && (ip.src == %s)' %
                         (port2['fixed_ips'][0]['ip_address'])}
        port1_acls_old = [aclport1_old1, aclport1_old2, aclport1_old3]
        aclport2_old1 = {'priority': 1002, 'direction': 'from-lport',
                         'lport': port2['id'], 'lswitch': lswitch_name,
                         'match': 'inport == %s && ip4 && (ip.src == %s)' %
                         (port2['id'], port2['fixed_ips'][0]['ip_address'])}
        aclport2_old2 = {'priority': 1002, 'direction': 'from-lport',
                         'lport': port2['id'], 'lswitch': lswitch_name,
                         'match': 'inport == %s && ip6 && (ip.src == %s)' %
                         (port2['id'], port2['fixed_ips'][1]['ip_address'])}
        aclport2_old3 = {'priority': 1002, 'direction': 'to-lport',
                         'lport': port2['id'], 'lswitch': lswitch_name,
                         'match': 'ip4 && (ip.src == %s)' %
                         (port1['fixed_ips'][0]['ip_address'])}
        port2_acls_old = [aclport2_old1, aclport2_old2, aclport2_old3]
        acls_old_dict = {'%s' % (port1['id']): port1_acls_old,
                         '%s' % (port2['id']): port2_acls_old}
        acl_obj_dict = {str(aclport1_old1): 'row1',
                        str(aclport1_old2): 'row2',
                        str(aclport1_old3): 'row3',
                        str(aclport2_old1): 'row4',
                        str(aclport2_old2): 'row5',
                        str(aclport2_old3): 'row6'}
        # NEW ACLs, allow IPv6 communication
        aclport1_new1 = {'priority': 1002, 'direction': 'from-lport',
                         'lport': port1['id'], 'lswitch': lswitch_name,
                         'match': 'inport == %s && ip4 && (ip.src == %s)' %
                         (port1['id'], port1['fixed_ips'][0]['ip_address'])}
        aclport1_new2 = {'priority': 1002, 'direction': 'from-lport',
                         'lport': port1['id'], 'lswitch': lswitch_name,
                         'match': 'inport == %s && ip6 && (ip.src == %s)' %
                         (port1['id'], port1['fixed_ips'][1]['ip_address'])}
        aclport1_new3 = {'priority': 1002, 'direction': 'to-lport',
                         'lport': port1['id'], 'lswitch': lswitch_name,
                         'match': 'ip6 && (ip.src == %s)' %
                         (port2['fixed_ips'][1]['ip_address'])}
        port1_acls_new = [aclport1_new1, aclport1_new2, aclport1_new3]
        aclport2_new1 = {'priority': 1002, 'direction': 'from-lport',
                         'lport': port2['id'], 'lswitch': lswitch_name,
                         'match': 'inport == %s && ip4 && (ip.src == %s)' %
                         (port2['id'], port2['fixed_ips'][0]['ip_address'])}
        aclport2_new2 = {'priority': 1002, 'direction': 'from-lport',
                         'lport': port2['id'], 'lswitch': lswitch_name,
                         'match': 'inport == %s && ip6 && (ip.src == %s)' %
                         (port2['id'], port2['fixed_ips'][1]['ip_address'])}
        aclport2_new3 = {'priority': 1002, 'direction': 'to-lport',
                         'lport': port2['id'], 'lswitch': lswitch_name,
                         'match': 'ip6 && (ip.src == %s)' %
                         (port1['fixed_ips'][1]['ip_address'])}
        port2_acls_new = [aclport2_new1, aclport2_new2, aclport2_new3]
        acls_new_dict = {'%s' % (port1['id']): port1_acls_new,
                         '%s' % (port2['id']): port2_acls_new}

        acls_new_dict_copy = copy.deepcopy(acls_new_dict)

        # Invoke _compute_acl_differences
        update_cmd = cmd.UpdateACLsCommand(self.driver._ovn,
                                           [lswitch_name],
                                           iter(ports),
                                           acls_new_dict
                                           )
        acl_dels, acl_adds =\
            update_cmd._compute_acl_differences(iter(ports),
                                                acls_old_dict,
                                                acls_new_dict,
                                                acl_obj_dict)
        # Sort the results for comparison
        for row in six.itervalues(acl_dels):
            row.sort()
        for row in six.itervalues(acl_adds):
            row.sort()
        # Expected Difference (Sorted)
        acl_del_exp = {lswitch_name: ['row3', 'row6']}
        acl_adds_exp = {lswitch_name:
                        [{'priority': 1002, 'direction': 'to-lport',
                          'match': 'ip6 && (ip.src == %s)' %
                          (port1['fixed_ips'][1]['ip_address'])},
                         {'priority': 1002, 'direction': 'to-lport',
                          'match': 'ip6 && (ip.src == %s)' %
                          (port2['fixed_ips'][1]['ip_address'])}]}
        self.assertEqual(acl_dels, acl_del_exp)
        self.assertEqual(acl_adds, acl_adds_exp)

        # make sure argument add_acl=False will take no affect in
        # need_compare=True scenario
        update_cmd_with_acl = cmd.UpdateACLsCommand(self.driver._ovn,
                                                    [lswitch_name],
                                                    iter(ports),
                                                    acls_new_dict_copy,
                                                    need_compare=True,
                                                    is_add_acl=False)
        new_acl_dels, new_acl_adds =\
            update_cmd_with_acl._compute_acl_differences(iter(ports),
                                                         acls_old_dict,
                                                         acls_new_dict_copy,
                                                         acl_obj_dict)
        for row in six.itervalues(new_acl_dels):
            row.sort()
        for row in six.itervalues(new_acl_adds):
            row.sort()
        self.assertEqual(acl_dels, new_acl_dels)
        self.assertEqual(acl_adds, new_acl_adds)

    def test__get_update_data_without_compare(self):
        lswitch_name = 'lswitch-1'
        port1 = {'id': 'port-id1',
                 'network_id': lswitch_name,
                 'fixed_ips': mock.Mock()}
        port2 = {'id': 'port-id2',
                 'network_id': lswitch_name,
                 'fixed_ips': mock.Mock()}
        ports = [port1, port2]
        aclport1_new = {'priority': 1002, 'direction': 'to-lport',
                        'match': 'outport == %s && ip4 && icmp4' %
                        (port1['id'])}
        aclport2_new = {'priority': 1002, 'direction': 'to-lport',
                        'match': 'outport == %s && ip4 && icmp4' %
                        (port2['id'])}
        acls_new_dict = {'%s' % (port1['id']): aclport1_new,
                         '%s' % (port2['id']): aclport2_new}

        # test for creating new acls
        update_cmd_add_acl = cmd.UpdateACLsCommand(self.driver._ovn,
                                                   [lswitch_name],
                                                   iter(ports),
                                                   acls_new_dict,
                                                   need_compare=False,
                                                   is_add_acl=True)
        lswitch_dict, acl_del_dict, acl_add_dict = \
            update_cmd_add_acl._get_update_data_without_compare()
        self.assertIn('neutron-lswitch-1', lswitch_dict)
        self.assertEqual({}, acl_del_dict)
        expected_acls = {'neutron-lswitch-1': [aclport1_new, aclport2_new]}
        self.assertEqual(expected_acls, acl_add_dict)

        # test for deleting existing acls
        acl1 = mock.Mock(
            match='outport == port-id1 && ip4 && icmp4')
        acl2 = mock.Mock(
            match='outport == port-id2 && ip4 && icmp4')
        acl3 = mock.Mock(
            match='outport == port-id1 && ip4 && (ip4.src == fake_ip)')
        lswitch_obj = mock.Mock(
            name='neutron-lswitch-1', acls=[acl1, acl2, acl3])
        with mock.patch('neutron.agent.ovsdb.native.idlutils.row_by_value',
                        return_value=lswitch_obj):
            update_cmd_del_acl = cmd.UpdateACLsCommand(self.driver._ovn,
                                                       [lswitch_name],
                                                       iter(ports),
                                                       acls_new_dict,
                                                       need_compare=False,
                                                       is_add_acl=False)
            lswitch_dict, acl_del_dict, acl_add_dict = \
                update_cmd_del_acl._get_update_data_without_compare()
            self.assertIn('neutron-lswitch-1', lswitch_dict)
            expected_acls = {'neutron-lswitch-1': [acl1, acl2]}
            self.assertEqual(expected_acls, acl_del_dict)
            self.assertEqual({}, acl_add_dict)

    def test_acl_protocol_and_ports_for_tcp_and_udp_number(self):
        sg_rule = {'port_range_min': None,
                   'port_range_max': None}

        sg_rule['protocol'] = str(const.PROTO_NUM_TCP)
        match = ovn_acl.acl_protocol_and_ports(sg_rule, None)
        self.assertEqual(' && tcp', match)

        sg_rule['protocol'] = str(const.PROTO_NUM_UDP)
        match = ovn_acl.acl_protocol_and_ports(sg_rule, None)
        self.assertEqual(' && udp', match)

    def test_acl_protocol_and_ports_for_ipv6_icmp_protocol(self):
        sg_rule = {'port_range_min': None,
                   'port_range_max': None}
        icmp = 'icmp6'
        expected_match = ' && icmp6'

        sg_rule['protocol'] = const.PROTO_NAME_ICMP
        match = ovn_acl.acl_protocol_and_ports(sg_rule, icmp)
        self.assertEqual(expected_match, match)

        sg_rule['protocol'] = str(const.PROTO_NUM_ICMP)
        match = ovn_acl.acl_protocol_and_ports(sg_rule, icmp)
        self.assertEqual(expected_match, match)

        sg_rule['protocol'] = const.PROTO_NAME_IPV6_ICMP
        match = ovn_acl.acl_protocol_and_ports(sg_rule, icmp)
        self.assertEqual(expected_match, match)

        sg_rule['protocol'] = n_const.PROTO_NAME_IPV6_ICMP_LEGACY
        match = ovn_acl.acl_protocol_and_ports(sg_rule, icmp)
        self.assertEqual(expected_match, match)

        sg_rule['protocol'] = str(const.PROTO_NUM_IPV6_ICMP)
        match = ovn_acl.acl_protocol_and_ports(sg_rule, icmp)
        self.assertEqual(expected_match, match)

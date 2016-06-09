#!/usr/bin/python
# -*- coding: utf-8 -*-
from __future__ import print_function, unicode_literals

import csv
import collections
import os
import re
import subprocess
import sys
import unicodedata


_groupname_strip_re = re.compile(r'[^A-Za-z0-9_/-]')

##################
# Config Section #
##################

# Groups that users get added to automatically
DEFAULT_GROUPS = ['ipausers']

# Character that separates groups in csv field
GROUP_SEP = '/'

# Mapping CSV file column number to named properties
CSV_MAP = {
    'member_of_groups': 12,
    'user_login': 5,
    'first_name': 6,
    'last_name': 7,
    'email_address': 8,
    'telephone_number': 9,
    'mobile_telephone_number': 10,
}

# List of used IPA fields with their corresponding command line tool flag name
IPA_CMDLINE_MAP = {
    'first_name': 'first',
    'last_name': 'last',
    'email_address': 'email',
    'telephone_number': 'phone',
    'mobile_telephone_number': 'mobile',
}


################
# Code Section #
################

DEV_NULL = open(os.devnull, 'wb')


def read_csv_file(filename):
    """Read the contents of a CVS file into a dict"""
    with open(filename) as file:
        csv_reader = csv.reader(file)
        next(csv_reader)  # skip header
        for line in csv_reader:
            entry = {}
            for key in CSV_MAP:
                entry[key] = line[CSV_MAP[key]]
            yield entry


def fix_csv_group_names(entries):
    group_descriptions = {}
    for entry in entries:
        original_group_name = entry['member_of_groups'].strip(' ' + GROUP_SEP)

        # Replace spaces and tabs with underscores
        group_name = '_'.join(original_group_name.split())

        # Replace umlaut characters
        for umlaut, replacement in zip('äöüÄÖÜ', 'ae oe ue Ae Oe Ue'.split()):
            group_name = group_name.replace(umlaut, replacement)

        # Strip remaining diacritical marks
        group_name = unicodedata.normalize('NFKD', group_name)   \
            .encode('ascii', 'ignore').decode('ascii')

        # Strip all remaining illegal characters
        group_name = _groupname_strip_re.sub('', group_name)
        group_names = group_name.split(GROUP_SEP)
        entry['member_of_groups'] = group_names
        group_descriptions.update(zip(group_names,
                                      original_group_name.split(GROUP_SEP)))
    return group_descriptions


def fix_csv_emails(entries):
    for entry in entries:
        email = entry['email_address']
        if ';' in email:
            entry['email_address'] = email.split(';')[0]


def fix_csv_zero_entries(entries, fields=('email_address',
                                          'telephone_number',
                                          'mobile_telephone_number')):
    for entry in entries:
        for field in fields:
            if entry[field].strip() == '0':
                entry[field] = ''


def parse_freeipa_output(output):
    """Parse the output from the FreeIPA command line tool"""
    entry = {}
    for line in output.strip().split('\n'):
        key, val = line.split(':', 1)
        entry[key.strip().lower().replace(' ', '_')] = val.strip()
    return entry


def query_ipa(usernames):
    """Query user information with the FreeIPA command line tool"""
    for username in usernames:
        try:
            yield parse_freeipa_output(
                subprocess.check_output(['ipa', 'user-show', '--all', username],
                                        stderr=subprocess.STDOUT)
            )
        except subprocess.CalledProcessError:
            yield {}


def find_user_differences(csv_entries, ipa_entries):
    """"""
    changes = {
        'user-mod': {},
        'user-add': {},
        'group-add-member': collections.defaultdict(list),
        'group-remove-member': collections.defaultdict(list),
    }
    for new, old in zip(csv_entries, ipa_entries):
        user = new['user_login']
        if old:
            user_changes = []
            for key, cmdline_key in IPA_CMDLINE_MAP.items():
                new_val = new.get(key, '').strip()
                old_val = old.get(key, '').strip()
                if new_val != old_val:
                    user_changes.append('--{0}={1}'.format(cmdline_key,
                                                           new_val))
            if user_changes:
                changes['user-mod'][user] = user_changes
        else:
            changes['user-add'][user] = \
                ['--{0}={1}'.format(cmdline_key, new.get(key, '').strip())
                 for key, cmdline_key in IPA_CMDLINE_MAP.items()]

        old_groups = set(
            filter(bool, old.get('member_of_groups', '').split(', '))
        )
        new_groups = set(
            list(filter(bool, new.get('member_of_groups', [])))
            + DEFAULT_GROUPS
        )
        for group in new_groups - old_groups:
            changes['group-add-member'][group].append('--users={}'.format(user))
        for group in old_groups - new_groups:
            changes['group-remove-member'][group]\
                .append('--users={}'.format(user))

    return changes


def find_group_changes(user_changes, group_descriptions):
    """Find newly added groups in changes"""
    changes = collections.defaultdict(list)
    for group in user_changes['group-add-member']:
        if subprocess.call(['ipa', 'group-show', group],
                           stdout=DEV_NULL, stderr=DEV_NULL) != 0:
            changes[group] = ['--desc={}'.format(group_descriptions[group])] \
                             if group in group_descriptions else []
    return changes


def commit_changes(changes):
    """Call FreeIPA command line tool to apply changes"""
    # order of operations is important
    for command in ['user-add', 'user-mod', 'group-add',
                    'group-add-member', 'group-remove-member']:
        for primary_key, args in changes[command].iteritems():
            subprocess.call(['ipa', '--no-prompt', command, primary_key]
                            + args  )


def main(filename):
    csv_entries = list(read_csv_file(filename))
    group_descriptions = fix_csv_group_names(csv_entries)
    fix_csv_emails(csv_entries)
    fix_csv_zero_entries(csv_entries)
    ipa_entries = query_ipa(entry['user_login'] for entry in csv_entries)

    changes = find_user_differences(csv_entries, ipa_entries)
    changes['group-add'] = find_group_changes(changes, group_descriptions)

    if not any(changes.itervalues()):
        print('No changes.')
        exit()

    print('The following changes will be applied:')
    print('  - Added users: {}'.format(len(changes['user-add'])))
    print('  - Modified users: {}'.format(len(changes['user-mod'])))
    print('  - Added groups: {}'.format(len(changes['group-add'])))
    print('  - Adding users to groups: {}'
          .format(len(changes['group-add-member'])))
    print('  - Removing users from groups: {}'
          .format(len(changes['group-remove-member'])))
    print()
    while True:
        answer = raw_input('Accept changes [y], abort [n], show details [d]: ')
        if answer.lower() == 'n':
            exit(2)
        elif answer.lower() == 'y':
            commit_changes(changes)
            exit(0)
        elif answer.lower() == 'd':
            import json
            print(json.dumps(changes, indent=2))


if __name__ == '__main__':
    if len(sys.argv) != 2:
        print("Usage:\npython ipa_import.py CSV_FILE_NAME")
        exit(1)
    main(*sys.argv[1:])

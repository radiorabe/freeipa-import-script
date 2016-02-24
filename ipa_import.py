#!/usr/bin/python
from __future__ import print_function

import csv
import collections
import os
import subprocess
import sys


DEFAULT_GROUPS = ['ipausers']
GROUP_SEP = '/'

CSV_MAP = {
    'member_of_groups': 4,
    'user_login': 5,
    'first_name': 6,
    'last_name': 7,
    'email_address': 8,
    'telephone_number': 9,
    'mobile_telephone_number': 10,
}

IPA_CMDLINE_MAP = {
    'first_name': 'first',
    'last_name': 'last',
    'email_address': 'email',
    'telephone_number': 'phone',
    'mobile_telephone_number': 'mobile',
}

DEV_NULL = open(os.devnull, 'wb')


def read_csv_file(filename):
    with open(filename) as file:
        csv_reader = csv.reader(file)
        next(csv_reader)  # skip header
        for line in csv_reader:
            entry = {}
            for key in CSV_MAP:
                entry[key] = line[CSV_MAP[key]]
            yield entry


def parse_freeipa_output(output):
    entry = {}
    for line in output.strip().split('\n'):
        key, val = line.split(':', 1)
        entry[key.strip().lower().replace(' ', '_')] = val.strip()
    return entry


def query_ipa(usernames):
    for username in usernames:
        try:
            yield parse_freeipa_output(
                subprocess.check_output(['ipa', 'user-show', '--all', username],
                                        stderr=subprocess.STDOUT)
            )
        except subprocess.CalledProcessError:
            yield {}


def main(filename):
    csv_entries = list(read_csv_file(filename))
    ipa_entries = query_ipa(entry['user_login'] for entry in csv_entries)
    changes = {
        'user-mod': {},
        'user-add': {},
        'group-add': collections.defaultdict(list),
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
                ['--{0}={1}'.format(cmdline_key, new.get(key, ''))
                 for key, cmdline_key in IPA_CMDLINE_MAP.items()]

        old_groups = set(
            filter(bool, old.get('member_of_groups', '').split(', '))
        )
        new_groups = set(
            filter(bool, new.get('member_of_groups', '').split(GROUP_SEP))
            + DEFAULT_GROUPS
        )
        for group in new_groups - old_groups:
            changes['group-add-member'][group].append('--users={}'.format(user))
        for group in old_groups - new_groups:
            changes['group-remove-member'][group]\
                .append('--users={}'.format(user))

    for group in changes['group-add-member']:
        if subprocess.call(['ipa', 'group-show', group],
                           stdout=DEV_NULL, stderr=DEV_NULL) != 0:
            changes['group-add'][group] = []

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
            # order of operations is important
            for command in ['user-add', 'user-mod', 'group-add',
                            'group-add-member', 'group-remove-member']:
                for primary_key, args in changes[command].iteritems():
                    subprocess.call(['ipa', '--no-prompt', command, primary_key]
                                    + args  )
            exit(0)
        elif answer.lower() == 'd':
            import json
            print(json.dumps(changes, indent=2))



if __name__ == '__main__':
    if len(sys.argv) != 2:
        print("Usage:\npython ipa_import.py CSV_FILE_NAME")
        exit(1)
    main(*sys.argv[1:])

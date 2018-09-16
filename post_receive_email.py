#!/usr/bin/env python3

import re
from smtplib import SMTP_SSL
from subprocess import Popen, check_output, PIPE, DEVNULL
import sys
import time
import traceback
from collections import defaultdict
from email.message import EmailMessage
from email.utils import formatdate
from io import StringIO

MAILINGLIST = 'hooks.mailinglist'
EMAILPREFIX = 'hooks.emailprefix'
SMTP_HOST = 'hooks.smtp-host'
SMTP_PORT = 'hooks.smtp-port'
SMTP_SENDER = 'hooks.smtp-sender'
SMTP_SENDER_PASSWORD = 'hooks.smtp-sender-password'
POST_RECEIVE_LOGFILE = 'hooks.post-receive-logfile'
DEBUG = 'hooks.debug'

class Mailer(object):
    def __init__(self, smtp_host, smtp_port,
                 sender, sender_password, recipients):
        self.smtp_host = smtp_host
        self.smtp_port = smtp_port
        self.sender = sender
        self.sender_password = sender_password
        self.recipients = recipients

    def send(self, subject, reply_to, message):
        if not self.recipients:
            return

        email = EmailMessage()
        email.set_content(message)
        email['From'] = self.sender
        email['Reply-To'] = reply_to
        email['To'] = ', '.join(self.recipients)
        email['Subject'] = subject

        with SMTP_SSL(self.smtp_host, self.smtp_port) as server:
            server.login(self.sender, self.sender_password)
            server.send_message(email)

def git_config_get(name, default=""):
    p = Popen(['git', 'config', '--get', name], 
            stdout=PIPE, stderr=DEVNULL, universal_newlines=True)
    # Cut off the last \n character.
    return p.stdout.read()[:-1]

def git_show(hash):
    s = check_output(['git', 'show', hash], 
            universal_newlines=True)
    return s

def git_show_format_str(rev, fmt):
    s = check_output(['git', 'show', 
            '--pretty=format:{}'.format(fmt) , '-s', rev],
            universal_newlines=True)
    return s

def git_rev_list_range_pretty(old_rev, new_rev):
    s = check_output(['git', 'rev-list', '--pretty',
            '{}..{}'.format(old_rev, new_rev)],
            universal_newlines=True)
    return s[:-1]

def parse_post_receive_line(l):
    return l.split()

def ref_type_name(ref):
    name = ref.split('/')[-1]
    if ref.startswith("refs/heads/"):
        return "branch", name
    elif ref.startswith("refs/tags/"):
        return "tag", name
    else:
        return "unknown", name

def is_dummy_rev(rev):
    return rev == "0000000000000000000000000000000000000000"

def short_hash(rev):
    return rev[:7]

def commit_subject(rev):
    return git_show_format_str(rev, "%s")

def commiter_email(rev):
    return git_show_format_str(rev, "%ce")

def escape_git_fmt_str(fmt):
    return fmt.replace("%","%%")

def process_new_branch(rev, name, mailer, config):
    subject = "{}new branch: ({}) at commit {}: {}".format(
                  config[EMAILPREFIX],
                  name,
                  short_hash(rev),
                  commit_subject(rev))
    reply_to = commiter_email(rev)
    message = git_show_format_str(rev,
"""\
Commiter: %cn <%ce>
Date: %cD
New branch: {}
Commit: %H
Subject: %s
Notes:
%N\
""".format(escape_git_fmt_str(name)))
    mailer.send(subject, reply_to, message)    

def process_delete_branch(rev, name, mailer, config):
    subject = "{}delete branch: ({})".format(
                  config[EMAILPREFIX],
                  name)
    reply_to = commiter_email(rev)
    now = formatdate(localtime=True)
    message = \
"""\
Date: {}
Deleted branch: {}\
""".format(now, name)
    mailer.send(subject, reply_to, message)    

def process_new_tag(rev, name, mailer, config):
    tag_commit_rev = git_show_format_str("{}^{{commit}}".format(rev), "%H")
    subject = "{}new tag: ({}) at commit {}: {}".format(
                  config[EMAILPREFIX],
                  name,
                  short_hash(tag_commit_rev),
                  commit_subject(tag_commit_rev))
    reply_to = commiter_email(tag_commit_rev)
    message = git_show(rev)
    mailer.send(subject, reply_to, message)    

def process_delete_tag(rev, name, mailer, config):
    tag_commit_rev = git_show_format_str("{}^{{commit}}".format(rev), "%H")
    subject = "{}delete tag: ({})".format(
                  config[EMAILPREFIX],
                  name)
    reply_to = commiter_email(tag_commit_rev)
    now = formatdate(localtime=True)
    message = \
"""\
Date: {}
Deleted tag: {}\
""".format(now, name)
    mailer.send(subject, reply_to, message)    

def process_unknown(old_rev, new_rev, ref_name, config):
    pass    

def is_descendant_commit(rev1, rev2):
    p = Popen(['git', 'merge-base', '--is-ancestor', rev1, rev2],  
                stdout=DEVNULL, universal_newlines=True)
    retcode = p.wait(timeout=1)
    if retcode == 0:
        return True
    elif retcode == 1:
        return False
    else:
        raise Exception("is_descendant_commit error")

# number of commits in (old_rev, new_rev]
def num_commits_in_range(old_rev, new_rev):
    s = check_output(['git', 'rev-list', '--count',
            '{}..{}'.format(old_rev, new_rev)],
            universal_newlines=True)
    return int(s)


def process_commit_range(old_rev, new_rev, branchname, mailer, config):
    if is_descendant_commit(old_rev, new_rev):
        process_new_commits(old_rev, new_rev, branchname, mailer, config)
    elif is_descendant_commit(new_rev, old_rev):
        process_forced_reset(old_rev, new_rev, branchname, mailer, config)
    else:
        process_forced_unknown(old_rev, new_rev, branchname, mailer, config)

def process_new_commits(old_rev, new_rev, branchname, mailer, config):
    n = num_commits_in_range(old_rev, new_rev)
    if n == 1:
        subject = "{}({}) new commit {}: {}".format(
                       config[EMAILPREFIX],
                       branchname,
                       short_hash(new_rev),
                       commit_subject(new_rev))
    elif n > 0:
        subject = "{}({}) {} new commits {}: {}".format(
                       config[EMAILPREFIX],
                       branchname,
                       n,
                       short_hash(new_rev),
                       commit_subject(new_rev))
    else:
        raise Exception("process 0 new commits")
    message = git_rev_list_range_pretty(old_rev, new_rev)
    reply_to = commiter_email(new_rev)                   
    mailer.send(subject, reply_to, message)    

def process_forced_reset(old_rev, new_rev, branchname, mailer, config):
    subject = "{}({}) forced reset to commit {}: {}".format(
                   config[EMAILPREFIX],
                   branchname,
                   short_hash(new_rev),
                   commit_subject(new_rev))
    message = git_show_format_str(new_rev,
"""\
Commiter: %cn <%ce>
Date: %cD
Branch: {}
Reset to commit: %H
Subject: %s
Notes:
%N\
""".format(escape_git_fmt_str(branchname)))
    reply_to = commiter_email(new_rev)                   
    mailer.send(subject, reply_to, message)    

def process_forced_unknown(old_rev, new_rev, branchname, mailer, config):
    subject = "{}({}) forced rewrite to commit {}: {}".format(
                   config[EMAILPREFIX],
                   branchname,
                   short_hash(new_rev),
                   commit_subject(new_rev))
    message = git_show_format_str(new_rev,
"""\
Commiter: %cn <%ce>
Date: %cD
Branch: {}
Most recent commit: %H
Subject: %s
Notes:
%N\
""".format(escape_git_fmt_str(branchname)))
    reply_to = commiter_email(new_rev)                   
    mailer.send(subject, reply_to, message)    

def post_receive(mailer, lines, config):
    commits = {}
    for line in lines:
        old_rev, new_rev, ref_name = parse_post_receive_line(line)
        ref_type, name = ref_type_name(ref_name)
        if ref_type == "branch":
            if is_dummy_rev(old_rev):
                process_new_branch(new_rev, name, mailer, config)
            elif is_dummy_rev(new_rev):
                process_delete_branch(old_rev, name, mailer, config)
            else:
                process_commit_range(old_rev, new_rev, name, mailer, config)
        elif ref_type == "tag":
            if is_dummy_rev(old_rev):
                process_new_tag(new_rev, name, mailer, config)
            elif is_dummy_rev(new_rev):
                process_delete_tag(old_rev, name, mailer, config)
        else:
            process_unknown(old_rev, new_rev, ref_name, config)    

def get_config_variables():
    def bool_from_str(s):
        return s and s[0] not in 'fF0'
    def optional(variable, default="", type_=str):
        config[variable] = type_(git_config_get(variable, default=default))
    def required(variable, type_=str):
        v = git_config_get(variable)
        if not v:
            raise RuntimeError('This script needs %s to work.' % variable)
        config[variable] = type_(v)
    def recipients(variable):
        v = git_config_get(variable)
        config[variable] = [r for r in re.split(' *, *| +', v) if r]

    config = {}
    optional(EMAILPREFIX)
    if config[EMAILPREFIX] and config[EMAILPREFIX][-1] != " ":
        config[EMAILPREFIX] += " "
    optional(DEBUG, False, bool_from_str)
    optional(POST_RECEIVE_LOGFILE, "/dev/null")
    required(SMTP_HOST)
    required(SMTP_PORT, int)
    required(SMTP_SENDER)
    required(SMTP_SENDER_PASSWORD)
    recipients(MAILINGLIST)
    return config

def main():
    log_file_path = git_config_get(POST_RECEIVE_LOGFILE)
    with open(log_file_path, 'a') as log_file:
        try:
            stdinlines = sys.stdin.readlines()
            config = get_config_variables()
            if config[DEBUG]:
                log_file.write('%s\n' % time.strftime('%Y-%m-%d %X'))
                log_file.writelines(stdinlines)
            mailer = Mailer(config[SMTP_HOST], config[SMTP_PORT],
                            config[SMTP_SENDER], config[SMTP_SENDER_PASSWORD],
                            config[MAILINGLIST])
            post_receive(mailer, stdinlines, config)
        except:
            log_file.write('%s\n' % time.strftime('%Y-%m-%d %X'))
            traceback.print_exc(file=log_file)

if __name__ == '__main__':
    main()

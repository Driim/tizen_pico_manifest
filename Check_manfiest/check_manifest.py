#!/usr/bin/python2
# coding: utf-8
# vim: tabstop=4 expandtab shiftwidth=4 softtabstop=4
import os
import sys
import re
import urllib
import urlparse
import shutil
import subprocess
import urllib
import argparse
from collections import defaultdict
import xml.etree.cElementTree as ET

from requests.auth import HTTPDigestAuth
from pygerrit.rest import GerritRestAPI


HEADER="""<?xml version="1.0" encoding="UTF-8"?>
<manifest>
"""
XML_ITEM="""  <project name="%s" path="%s" groups="%s"/>\n"""
FOOTER="""</manifest>"""


class GerritClient(object):
    def __init__(self, gerrit_url, user=None, passwd=None):
        auth = None
        if user and passwd:
            auth = HTTPDigestAuth(user, passwd)

        self.api = GerritRestAPI(url='https://review.tizen.org/gerrit',  auth=auth)

    def get_branches(self, project_name):
        quoted_path = urllib.quote(project_name,safe='')
        branches = self.api.get("/projects/%s/branches/" % quoted_path)

        return [br['ref'].replace("refs/heads/", "") for br in branches]


def parse_buildxml(baseurl):
    """Get build id and build target of remote repo"""
    if not baseurl.endswith('/'):
        baseurl += '/'
    repomd_url = urlparse.urljoin(baseurl, 'build.xml')
    fobj = urllib.urlopen(repomd_url)
    root = ET.fromstring(fobj.read())
    data = {}
    data['id'] = root.findtext('id')
    data['buildtargets'] = []
    targets = root.find('buildtargets')
    if targets:
        for target in targets.findall('buildtarget'):
            data['buildtargets'].append(target.get('name'))
    return data

def parse_manifest(fname):
    etree = ET.parse(fname)
    root = etree.getroot()
    nodes = root.findall('project')
    projects = {node.get('name'):dict(node.items()) for node in nodes}
    return projects

def diff_projects(file1, file2):
    """Compare the difference between two manifest file"""
    projects_1 = parse_manifest(file1)
    projects_2 = parse_manifest(file2)
    ret = defaultdict(list)
    for prj_name in projects_1:
        if prj_name not in projects_2:
            ret['Removed'].append(prj_name)
    for prj_name, prj_info in projects_2.items():
        if prj_name not in  projects_1:
            ret['Added'].append(prj_name)
        else:
            try:
                old_revision = prj_info['revision']
                new_revision = projects_1[prj_name]['revision']
            except KeyError:
                pass
            else:
                if old_revision != new_revision:
                    ret['Changed'].append(
                            dict(name=prj_name, old_revision=old_revision, new_revision=new_revision)
                            )
    return ret

def get_rev_parse(project_dir, treeish):
    cmd = 'git --git-dir=%s rev-parse %s' % (project_dir, treeish)
    try:
        revision = subprocess.check_output(cmd.split()).strip()
    except subprocess.CalledProcessError, err:
        print head, 'not found in', prj
        return None

    return revision

def gen_repo_manifest(projects, profile):
    """generate manifest for specified profile using projects list"""
    manifest_head = HEADER
    manifest_body = ""
    manifest_tail = FOOTER

    for project in sorted(projects):
        manifest_body +=XML_ITEM %(project, project, profile)

    return manifest_head + manifest_body + manifest_tail

def get_rev_parse(project_dir, treeish):
    cmd = 'git --git-dir=%s rev-parse %s' % (project_dir, treeish)
    try:
        revision = subprocess.check_output(cmd.split()).strip()
    except subprocess.CalledProcessError, err:
        print head, 'not found in', prj
        return None

    return revision

if __name__ == '__main__':
    repo_alias = {
        'common-latest': 'http://download.tizen.org/snapshots/tizen/common/latest/',
        'ivi-latest': 'http://download.tizen.org/snapshots/tizen/ivi/latest/',
	    'mobile-latest': 'http://download.tizen.org/snapshots/tizen/mobile/latest',
        'tv-latest': 'http://download.tizen.org/snapshots/tizen/tv/latest/',
        'wearable-latest': 'http://download.tizen.org/snapshots/tizen/wearable/latest/',
    }
    parser = argparse.ArgumentParser(description="Check manifest changes")
    parser.add_argument('--url', required=True, help='snaptshot repo url contain builddata directory')
    parser.add_argument('--diff', action='store_true', help='check the difference between two projects xml file')
    parser.add_argument('--update', action='store_true', help='update manifest xml file')
    parser.add_argument('--tizen-src', required=True, help='specify tizen source directory')
    parser.add_argument('-p', '--profile', required=True, help='profile, valid value is ivi and common')
    parser.add_argument('--sync', action='store_true', help='run repo sync')
    parser.add_argument('--outdir', help='output directory')
    args = parser.parse_args()

    args.tizen_src = os.path.abspath(os.path.expanduser(args.tizen_src))
    if args.url in repo_alias:
        args.url = repo_alias[args.url]

    data = parse_buildxml(args.url)

    projects = {}
    manifest_file = None
    local_manifest = os.path.join(args.tizen_src, '.repo/manifests', args.profile, 'projects.xml')

    for buildtarget in data['buildtargets']:
        manifest_file_url = args.url + '/builddata/manifest/' + data['id'] + '_' + buildtarget + '.xml'
        print 'downloading', manifest_file_url
        manifest_file = os.path.basename(manifest_file_url)
        urllib.urlretrieve(manifest_file_url, manifest_file)
        projects.update(parse_manifest(manifest_file))

    if args.update:
        subprocess.call(["git", "reset", "--hard", "HEAD"],
                cwd=os.path.join(args.tizen_src, '.repo/manifests'))
        manifest_string = gen_repo_manifest(projects, args.profile)
        with open(manifest_file, 'w') as fobj:
            fobj.write(manifest_string)
        ret = diff_projects(local_manifest, manifest_file)
        shutil.copy(manifest_file, local_manifest)
        if 'Removed' in ret:
            print len(ret['Removed']), "projects have been removed"
            for prj in ret['Removed']:
                print "\t", prj

        if 'Added' in ret:
            print len(ret['Added']), "projects have been added"
            for prj in ret['Added']:
                print "\t", prj
            packages_no_acc_branches = []
            gc = GerritClient('https://review.tizen.org/gerrit','<usrname>','<passwd>')
            for prj in ret['Added']:
                if not 'accepted/tizen_'+args.profile in gc.get_branches(prj):
                    packages_no_acc_branches.append(prj)

            if packages_no_acc_branches:
                print "%d packages have no accepted/tizen_%s branch" % \
                                   (len(packages_no_acc_branches), args.profile)
                for prj in packages_no_acc_branches:
                    print "\t", prj

    if args.sync:
        cmd = "repo sync -f"
        print "Running", cmd
        try:
            subprocess.check_output(cmd.split(), stderr=subprocess.STDOUT, cwd=args.tizen_src)
        except subprocess.CalledProcessError, err:
            print re.findall("revision .* in .* not found", err.output)

    if args.diff:
        tizen_source = os.path.abspath(args.tizen_src)
        print len(projects), "projects in total"
        print "found out if tizen source version mismath with obs "
        head = 'HEAD' if not args.profile else 'tizen-gerrit/accepted/tizen_' + args.profile

        report_file = 'tizen_%s_revision_diff.csv' % args.profile
        if args.outdir:
            if not os.path.exists(args.outdir):
                os.mkdir(args.outdir)
            out_file = os.path.join(args.outdir, report_file)
        else:
            out_file = report_file
        output = open(out_file, 'w')
        result = ['Project name,OBS revision,accepted branch revision,OBS is newer']

        for prj, prjinfo in projects.items():
            local_project = os.path.join(tizen_source, prj, '.git')
            if not os.path.exists(local_project):
                print 'remote project: %s does not exist locally' % prj
                continue
            version = get_rev_parse(local_project, head)
            prjinfo['revision'] = get_rev_parse(local_project, prjinfo['revision'])
            rev_list = subprocess.check_output(['git', '--git-dir=' + local_project, 'rev-list', head])
            newer = 'Y'
            if prjinfo['revision'] in rev_list:
                newer = 'N'
            if version != prjinfo['revision']:
                result.append(','.join([prj, prjinfo['revision'], version, newer]))
                #TODO: update accepted/tizen_{ivi,common} branch using OBS revision:prjinfo['revision']

        output.write('\n'.join(sorted(result)))
        print "Save to", out_file

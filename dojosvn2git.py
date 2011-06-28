#!/usr/bin/env python

# Copyright (c) 2011 Chris Barber <chris@cb1inc.com>
# All rights reserved.
#
# Redistribution and use in source and binary forms, with or without
# modification, are permitted provided that the following conditions are met:
#
# 1. Redistributions of source code must retain the above copyright
#    notice, this list of conditions and the following disclaimer.
# 2. Redistributions in binary form must reproduce the above copyright
#    notice, this list of conditions and the following disclaimer in the
#    documentation and/or other materials provided with the distribution.
# 3. The name of the author may not be used to endorse or promote products
#    derived from this software without specific prior written permission.
#
# THIS SOFTWARE IS PROVIDED BY THE COPYRIGHT HOLDERS AND CONTRIBUTORS
# "AS IS" AND ANY EXPRESS OR IMPLIED WARRANTIES, INCLUDING, BUT NOT
# LIMITED TO, THE IMPLIED WARRANTIES OF MERCHANTABILITY AND FITNESS FOR
# A PARTICULAR PURPOSE ARE DISCLAIMED. IN NO EVENT SHALL THE COPYRIGHT OWNER OR
# CONTRIBUTORS BE LIABLE FOR ANY DIRECT, INDIRECT, INCIDENTAL, SPECIAL,
# EXEMPLARY, OR CONSEQUENTIAL DAMAGES (INCLUDING, BUT NOT LIMITED TO,
# PROCUREMENT OF SUBSTITUTE GOODS OR SERVICES; LOSS OF USE, DATA, OR
# PROFITS; OR BUSINESS INTERRUPTION) HOWEVER CAUSED AND ON ANY THEORY OF
# LIABILITY, WHETHER IN CONTRACT, STRICT LIABILITY, OR TORT (INCLUDING
# NEGLIGENCE OR OTHERWISE) ARISING IN ANY WAY OUT OF THE USE OF THIS
# SOFTWARE, EVEN IF ADVISED OF THE POSSIBILITY OF SUCH DAMAGE.

# 
# Overview:
#   This utility will migrate commits from Dojo's official Subversion repo
#   to your own git repo.  It will combine Dojo's separate trunks for dojo,
#   dijit, dojox, util, and demos into a single repo including branches and
#   tags.
#
# Usage:
#   python dojosvn2git.py [<repo dir>]
#
# Examples:
#   Start fresh, creates new repo named "dojo-toolkit":
#     python dojosvn2git.py dojo-toolkit
#
#   Create or update an existing repo named "dojo-toolkit" and push to github:
#     python dojosvn2git.py dojo-toolkit my-github-account
#
#   Use a custom name:
#     python dojosvn2git.py my-dojo-repo
#
#   Update from within the repo and push to github
#     python dojosvn2git.py . my-github-account
#
# Dependencies:
#   python
#   python-svn
#   git
#
# Note:
#   This tool is written by a Python noob. Don't hate.
#

import os, sys, subprocess, pysvn, time, re, shutil
from subprocess import Popen, PIPE, STDOUT
from math import floor

class Repo(object):
	
	def __init__(self, repo_path, remote_repo_username):
		if repo_path == ".":
			repo_path = os.getcwd()
		
		self.repo_path				= repo_path
		self.repo_name				= os.path.basename(repo_path)
		self.remote_repo_username	= remote_repo_username
		self.laps					= []
		self.svn_client				= pysvn.Client()
		self.num_commits			= 0
	
	def go(self):
		svn_url				= "http://svn.dojotoolkit.org/src"
		local_revid			= 15378 # release 1.2
		start_revid			= local_revid
		new_repo			= False
		do_checkout			= False
		branches_touched	= []
		branches_deleted	= []
		tags_touched		= []
		start_time			= time.time()
		svnrev_file			= os.path.join(self.repo_path, ".svnrev")
		last_run_new_branch	= False
		
		try:
			if self.is_locked():
				self.logln("\nDetected another instance of the tool already running, exiting")
				return 1
			
			if os.path.isdir(self.repo_path):
				self.create_lock()
				
				if os.path.isfile(svnrev_file):
					# need to figure out what version we're on
					svnrev_file = open(svnrev_file)
					rev = svnrev_file.readline()
					svnrev_file.close()
					
					if len(rev) and int(rev) >= local_revid:
						start_revid = local_revid = int(rev)
					else:
						self.logln("\nUnable to read a valid last svn rev from the .svnrev file")
						self.delete_lock()
						return 1
					
					if self.git_current_branch() != "master":
						self.git_checkout("master")
				else:
					self.logln("""\nRepo path "%s" is missing the .svnrev file!""" % self.repo_path)
					self.delete_lock()
					return 1
			else:
				# initialize the repo
				new_repo = True
				self.git_init()
				
				self.create_lock()
				
				# export the files
				self.log("\nDoing initial Dojo 1.2 svn checkout... ")
				self.svn_client.checkout(svn_url + "/dojo/trunk", os.path.join(self.repo_path, "dojo"), recurse=True, revision=pysvn.Revision(pysvn.opt_revision_kind.number, local_revid))
				self.svn_client.checkout(svn_url + "/dijit/trunk", os.path.join(self.repo_path, "dijit"), recurse=True, revision=pysvn.Revision(pysvn.opt_revision_kind.number, local_revid))
				self.svn_client.checkout(svn_url + "/dojox/trunk", os.path.join(self.repo_path, "dojox"), recurse=True, revision=pysvn.Revision(pysvn.opt_revision_kind.number, local_revid))
				self.svn_client.checkout(svn_url + "/util/trunk", os.path.join(self.repo_path, "util"), recurse=True, revision=pysvn.Revision(pysvn.opt_revision_kind.number, local_revid))
				self.svn_client.checkout(svn_url + "/demos/trunk", os.path.join(self.repo_path, "demos"), recurse=True, revision=pysvn.Revision(pysvn.opt_revision_kind.number, local_revid))
				self.logln("done")
				
				self.log("Cleaning up .svn directories and finding empty directories... ")
				self.process_svn_dir(self.repo_path, True, False)
				self.logln("done")
				
				# add the files to git
				for project in ("dojo", "dijit", "dojox", "util", "demos"):
					self.git_add(project)
				
				# get the 1.2 log message
				log = self.svn_client.log(
					svn_url,
					revision_start=pysvn.Revision(pysvn.opt_revision_kind.number, local_revid),
					revision_end=pysvn.Revision(pysvn.opt_revision_kind.number, local_revid),
					discover_changed_paths=False
				)
				
				if (len(log)):
					# commit!
					self.git_commit(log[0]["message"], local_revid, log[0]["author"], log[0]["date"])
				else:
					self.logln("Error getting log info for rev %s" % local_revid)
					self.delete_lock()
					return 1
			
			svn_info  = self.svn_client.info2(svn_url, recurse=False)
			svn_revid = svn_info[0][1].rev.number
			
			if local_revid >= svn_revid:
				self.logln("You're up-to-date at revision %s" % local_revid)
				self.delete_lock()
				return 0
			
			while local_revid < svn_revid:
				
				local_revid += 1
				to_revid = local_revid + 100
				if to_revid > svn_revid:
					to_revid = svn_revid
				
				self.logln("\nFetching svn log history from rev %s to %s" % (local_revid, to_revid))
				log = self.svn_client.log(
					svn_url,
					revision_start=pysvn.Revision(pysvn.opt_revision_kind.number, local_revid),
					revision_end=pysvn.Revision(pysvn.opt_revision_kind.number, to_revid),
					discover_changed_paths=True
				)
				
				for rev in log:
					local_revid = rev["revision"].number
					rev_start_time = time.time()
					how_much_longer = self.how_long(start_revid, local_revid, svn_revid)
					if how_much_longer:
						self.logln("\n-- Rev %s ---------------------------------- (%s)" % (local_revid, how_much_longer))
					else:
						self.logln("\n-- Rev %s ----------------------------------" % local_revid)
					self.logln("{0} changed path{1}".format(len(rev["changed_paths"]), "s" if len(rev["changed_paths"]) != 1 else ""))
					
					tag = False
					files = {}
					dirs = []
					file_count = 0
					force_rev_update_on_master = False
					
					# sort each changed file based on the branch
					for changed_path in rev["changed_paths"]:
						self.log(" %s %s" % (changed_path.action, changed_path.path))
						
						parts = changed_path.path.strip("/").split("/")
						project_dir = parts.pop(0).lower()
						
						# check if this is a project we care about
						if project_dir not in ("branches", "tags", "dojo", "dijit", "dojox", "util", "demos"):
							self.logln("""... "%s" is not a project we care about, skipping """ % project_dir)
							continue
						
						# this means this changed_path was the start of a new project folder, so skip it
						if len(parts) < 1:
							self.logln("... path not deep enough, skipping")
							continue
						
						ver_dir = parts.pop(0)
						file_path = "/".join(parts)
						
						if project_dir == "tags":
							if changed_path.action == "A":
								# tag this commit
								tag = ver_dir
								self.logln()
							elif changed_path.action == "D":
								if self.git_tag_exists(tag):
									self.logln("... deleting tag");
									self.git_delete_tag(ver_dir)
								else:
									self.logln("... tag does not exist");
							else:
								self.logln("... tags with a %s action are not supported" % changed_path.action)
							continue
						
						if not len(file_path):
							if changed_path.action == "D":
								if project_dir == "branches":
									if self.git_branch_exists(ver_dir):
										self.logln("... deleting branch")
										branches_deleted.append(ver_dir)
										self.git_delete_branch(ver_dir)
									else:
										self.logln("... branch does not exist")
								else:
									self.logln("... deleting of projects not supported")
							else:
								self.logln("... path not deep enough, skipping")
							continue
						
						if changed_path.action == "D":
							# if we're deleting something, don't bother to get the info, just add it to be deleted
							branch = ver_dir if project_dir == "branches" else "master"
							if not branch in files:
								files[branch] = {}
							url = svn_url + changed_path.path
							if not url in files[branch]:
								files[branch][url] = { "project_dir":"" if project_dir == "branches" else project_dir, "action":changed_path.action, "file_path":file_path }
							self.logln()
						else:
							# detect if we're creating a new branch
							if project_dir == "branches" and (not self.git_branch_exists(ver_dir) or last_run_new_branch):
								branch = ver_dir
								if not branch in files:
									files[branch] = {}
								url = svn_url + changed_path.path
								if not url in files[branch]:
									files[branch][url] = { "project_dir":"", "action":changed_path.action, "file_path":"" }
								self.logln("""... detected new branch "%s" """ % branch)
								last_run_new_branch = True
								continue
							else:
								last_run_new_branch = False
							
							# get info for all files for this path
							rev_info = self.svn_client.info2(svn_url + changed_path.path.replace(" ", "%20"), recurse=True, revision=pysvn.Revision(pysvn.opt_revision_kind.number, local_revid))
							
							if len(rev_info) == 1:
								# if only one file, then add it
								if rev_info[0][1].kind == pysvn.node_kind.file:
									branch = ver_dir if project_dir == "branches" else "master"
									if not branch in files:
										files[branch] = {}
									url = svn_url + changed_path.path.replace(" ", "%20")
									if not url in files[branch]:
										files[branch][url] = { "project_dir":"" if project_dir == "branches" else project_dir, "action":changed_path.action, "file_path":file_path }
								elif rev_info[0][1].kind == pysvn.node_kind.dir:
									dirs.append({ "branch":branch, "project_dir":"" if project_dir == "branches" else project_dir, "file_path":file_path })
								self.logln()
							else:
								# if more than one file for this path, loop and add each
								self.logln("... directory with {0} file{1}".format(len(rev_info), "s" if len(rev_info) != 1 else ""))
								first = True
								for rev_file in rev_info:
									if not first:
										self.logln("   > %s [%s]" % (rev_file[0], rev_file[1].kind))
										if rev_file[1].kind == pysvn.node_kind.file:
											branch = ver_dir if project_dir == "branches" else "master"
											if not branch in files:
												files[branch] = {}
											url = rev_file[1].URL
											if not url in files[branch]:
												files[branch][url] = { "project_dir":"" if project_dir == "branches" else project_dir, "action":changed_path.action, "file_path":file_path + "/" + rev_file[0] }
										elif rev_info[0][1].kind == pysvn.node_kind.dir:
											dirs.append({ "branch":branch, "project_dir":"" if project_dir == "branches" else project_dir, "file_path":file_path + "/" + rev_file[0] })
									first = False
					
					for branch in files:
						self.logln("On branch %s" % branch)
						for url in files[branch]:
							entry = files[branch][url]
							# print " %s %s -> %s" % (entry["action"], url, os.path.join(self.repo_path, entry["project_dir"], entry["file_path"]))
							
							if self.git_current_branch() != branch:
								if not self.git_branch_exists(branch):
									self.git_create_branch(branch)
								self.git_checkout(branch)
								if branch != "master":
									force_rev_update_on_master = True
								if branch not in branches_touched:
									branches_touched.append(branch)
							
							if entry["file_path"] == "":
								# nothing to do
								continue
							
							if entry["action"] == "A" or entry["action"] == "M":
								# export the file from svn
								dest = os.path.join(self.repo_path, entry["project_dir"], entry["file_path"])
								dest_dir = os.path.dirname(dest)
								if not os.path.isdir(dest_dir):
									os.makedirs(dest_dir)
								# print "Exporting %s to %s" % (url, dest)
								self.svn_client.export(url.replace(" ", "%20"), dest, recurse=False, ignore_externals=True, revision=pysvn.Revision(pysvn.opt_revision_kind.number, local_revid))
								self.git_add(os.path.join(entry["project_dir"], entry["file_path"]))
							
							elif entry["action"] == "D":
								# delete the file
								self.git_rm(os.path.join(entry["project_dir"], entry["file_path"]))
						
						# check if any of the directories we encountered are empty
						for directory in dirs:
							if directory["branch"] == branch:
								full_dir = os.path.join(self.repo_path, directory['project_dir'], directory['file_path'])
								if not os.path.isdir(full_dir):
									os.makedirs(full_dir)
								self.process_svn_dir(full_dir, False, True)
						
						# need to run git status
						modified_files = self.git_status()
						file_count += len(modified_files)
						
						if len(modified_files) > 0:
							# git commit!
							self.git_commit(rev["message"], local_revid, rev["author"], rev["date"])
						else:
							print "No changes detected"
					
					# make sure we're back on master
					if self.git_current_branch() != "master":
						self.git_checkout("master")
					
					# if this rev is a tag, then tag it!
					if tag and not self.git_tag_exists(tag):
						self.git_create_tag(tag)
						tags_touched.append(tag)
					
					if file_count == 0 or (force_rev_update_on_master and "master" not in files):
						self.git_commit("Updating svn sync rev", local_revid, None, rev["date"])
					
					rev_total_time = time.time() - rev_start_time
					self.lap(rev_total_time)
			
			self.logln("\nRepo is now synced to rev %s" % local_revid)
			
			if self.num_commits > 0 and self.remote_repo_username != "":
				self.logln("\nPushing changes to remote repository")
				if new_repo:
					self.git_remote_add(self.remote_repo_username)
				self.git_push("master")
				
				for branch in branches_touched:
					if branch and branch != 'master':
						self.git_push(branch)
				
				if self.git_current_branch() != "master":
					self.git_checkout("master")
				
				for branch in branches_deleted:
					if branch and branch != 'master':
						self.git_push_deleted_branch(branch)
				
				if len(tags_touched):
					self.git_push_tags()
			
			total_time = time.time() - start_time
			total_hours = int(floor(total_time / 3600))
			total_minutes = int(floor(float(total_time) / 60.0)) - (total_hours * 60)
			total_seconds = int(round(total_time % 60))
			
			if total_hours == 0:
				if total_minutes == 0:
					self.logln("\nCompleted in %s seconds" % total_seconds)
				else:
					self.logln("\nCompleted in %s minutes, %s seconds" % (total_minutes, total_seconds))
			else:
				self.logln("\nCompleted in %s hours, %s minutes, %s seconds" % (total_hours, total_minutes, total_seconds))
			
			if self.num_commits > 0 and self.remote_repo_username == "":
				self.logln("\nNext steps:")
				if new_repo:
					self.logln("  git remote add origin git@github.com:<YOUR ACCOUNT>/%s.git" % self.repo_name)
					self.logln("  git push -u origin master")
				else:
					self.logln("  git push origin master")
				for branch in branches_touched:
					if branch and branch != 'master' and branch not in branches_deleted:
						self.logln("  git checkout %s" % branch)
						if new_repo:
							self.logln("  git push -u origin %s" % branch)
						else:
							self.logln("  git push origin %s" % branch)
				if len(branches_touched) or 'master' not in branches_touched:
					self.logln("  git checkout master")
				for branch in branches_deleted:
					if branch and branch != 'master':
						self.logln("  git push origin :%s" % branch)
				if len(tags_touched):
					self.logln("  git push --tags")
		except Exception:
			self.delete_lock()
			raise
		
		self.delete_lock()
		return 0
	
	def git_init(self):
		self.logln("""\nCreating new git repo "%s" """ % self.repo_name)
		
		self.run("""git init "%s" """ % self.repo_path, ".")
		
		readme_file = "README"
		file = open(os.path.join(self.repo_path, readme_file), 'w')
		file.write("Unofficial Dojo Toolkit Mirror\nhttp://dojotoolkit.org/\n")
		file.close()
		self.git_add(readme_file)
		
		ignore_file = ".gitignore"
		file = open(os.path.join(self.repo_path, ignore_file), 'w')
		file.write("._*\n.svn\n.lock\n")
		file.close()
		self.git_add(ignore_file)
		
		self.git_commit("Initialized repo and added README, .gitignore, and .svnrev files.")
	
	def git_add(self, file):
		self.logln(" A %s" % file)
		self.run("""git add "%s" """ % file)
	
	def git_rm(self, file):
		self.logln(" D %s" % file)
		if os.path.exists(os.path.join(self.repo_path, file)):
			self.run("""git rm -rf "%s" """ % file)
	
	def git_status(self):
		regex = re.compile('([^\s]+)\s+(.*)', re.MULTILINE)
		files = self.run("git status --porcelain")
		if len(files):
			if files[len(files)-1] == '':
				files.pop()
		return regex.findall(files)
	
	def git_commit(self, log, rev=0, author=None, date=None):
		log = log.strip().replace("\\", "\\\\").replace('"', '\\"').replace("!", "\"'!'\"").replace('$', '\\$')
		
		if rev > 0:
			log += " [[%s]]" % rev
		
		svnrev_file = ".svnrev"
		file = open(os.path.join(self.repo_path, svnrev_file), 'w')
		file.write(str(rev))
		file.close()
		self.git_add(svnrev_file)
		
		info = ""
		if author != None:
			info += """--author="%s <nobody@dojotoolkit.org>" """ % author
		if date != None:
			info += """--date="%s" """ % int(date)
		
		self.logln("""Committing "%s" """ % log)
		self.run("git commit -a -q %s -m %s" % (info, '"' + log + '"'))
		
		self.num_commits += 1
	
	def git_checkout(self, branch):
		self.logln("""Switching to branch "%s" """ % branch)
		self.run("git checkout %s" % branch)
	
	def git_branch_list(self):
		return map(lambda s: s[2:], self.run("git branch --no-color").split("\n"))
	
	def git_branch_exists(self, branch):
		return branch in self.git_branch_list()
	
	def git_create_branch(self, branch):
		self.logln("""Creating branch "%s" """ % branch)
		self.run("""git branch "%s" """ % branch)
	
	def git_delete_branch(self, branch):
		self.logln("""Deleting branch "%s" """ % branch)
		self.run("""git branch -D "%s" """ % branch)
	
	def git_current_branch(self):
		for branch in self.run("git branch --no-color").split("\n"):
			if branch[0] == '*':
				return branch[2:]
		return 'master'
	
	def git_tag_exists(self, tag):
		tags = self.run("git tag").split("\n")
		return tag in tags
	
	def git_create_tag(self, tag):
		self.logln("""Creating tag "%s" """ % tag)
		self.run("""git tag -a "%s" -m "Adding tag %s" """ % (tag, tag))
	
	def git_delete_tag(self, tag):
		self.logln("""Deleting tag "%s" """ % tag)
		self.run("""git tag -d "%s" """ % tag)
	
	def git_remote_add(self, user):
		self.logln("Adding remote origin git@github.com:%s/%s.git" % (user, self.repo_path))
		self.run("git remote add origin git@github.com:%s/%s.git" % (user, self.repo_path))
	
	def git_push(self, branch, upstream=False):
		self.logln("""Pushing branch "%s" """ % branch)
		if self.git_current_branch() != branch:
			self.git_checkout(branch)
		if upstream:
			self.run("git push -u origin %s" % branch)
		else:
			self.run("git push origin %s" % branch)
	
	def git_push_deleted_branch(self, branch):
		self.logln("""Deleting remote branch "%s" """ % branch)
		self.run("git push origin :%s" % branch)
	
	def git_push_tags(self):
		self.logln("Pushing tags")
		self.run("git push --tags")
	
	def run(self, cmd, cwd=None):
		if cwd == None:
			cwd = self.repo_path
		# self.logln("Running command: %s" % cmd)
		
		proc = Popen(cmd, shell=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, cwd=cwd)
		return_code = proc.wait()
		if return_code == 0:
			return proc.stdout.read()
		else:
			raise RuntimeError("Failed running command %s return code=%s" % (cmd, return_code))
	
	def lap(self, seconds):
		x = len(self.laps)
		while x >= 250:
			self.laps.pop(0)
			x -= 1
		self.laps.append(seconds)

	def how_long(self, start_rev, current_rev, end_rev):
		revs_left = end_rev - current_rev
		percent = int(floor(float(current_rev - start_rev) / float(end_rev - start_rev) * 100.0))
		
		x = len(self.laps)
		if x == 0:
			return False
		
		total = sum(self.laps)
		average_time = float(total) / x
		estimated_time = revs_left * average_time
		
		hours = int(floor(estimated_time / 3600))
		minutes = int(floor(float(estimated_time) / 60.0)) - (hours * 60)
		seconds = int(round(estimated_time % 60))
		
		if hours == 0:
			if minutes == 0:
				return "%d%% %d revs left, %d sec remaining" % (percent, revs_left, seconds)
			return "%d%% %d revs left, %d min %d sec remaining" % (percent, revs_left, minutes, seconds)
		return "%d%% %d revs left, %d hrs %d min %d sec remaining" % (percent, revs_left, hours, minutes, seconds)
	
	def process_svn_dir(self, path, recurse, do_add):
		delete_svn_dir = False
		files = os.listdir(path)
		
		if len(files) == 0:
			ignore_file = self.create_gitignore(path)
			if do_add:
				self.git_add(ignore_file)
		elif len(files) == 1 and files[0] == '.svn':
			delete_svn_dir = True
			props = self.svn_client.proplist(path)
			if len(props) and "svn:ignore" in props[0][1]:
				ignore_file = self.create_gitignore(path, props[0][1]["svn:ignore"])
			else:
				ignore_file = self.create_gitignore(path)
			if do_add:
				self.git_add(ignore_file)
		else:
			if recurse:
				for filename in files:
					p = os.path.join(path, filename)
					if os.path.isdir(p) and filename != ".svn" and filename != ".git":
						self.process_svn_dir(p, recurse, do_add)
			delete_svn_dir = True
		
		if delete_svn_dir and os.path.isdir(os.path.join(path, ".svn")):
			shutil.rmtree(os.path.join(path, ".svn"))
	
	def create_gitignore(self, path, contents = None):
		ignore_file = os.path.join(path, ".gitignore")
		ignore = open(ignore_file, 'w')
		if contents != None:
			ignore.write(contents)
		ignore.close()
		return os.path.abspath(ignore_file).replace(os.path.abspath(self.repo_path), "").lstrip("/")
	
	def create_lock(self):
		lock = open(os.path.join(self.repo_path, ".lock"), 'w')
		lock.write("Remove this file to unlock this repo")
		lock.close()
	
	def is_locked(self):
		return os.path.exists(os.path.join(self.repo_path, ".lock"))
	
	def delete_lock(self):
		lock = os.path.join(self.repo_path, ".lock")
		if os.path.exists(lock):
			os.remove(lock)
	
	def log(self, s=""):
		sys.stdout.write(s)
		sys.stdout.flush()

	def logln(self, s=""):
		sys.stdout.write(s)
		sys.stdout.write("\n")
		sys.stdout.flush()

if __name__ == "__main__":
	print "Dojo Toolkit svn->git Tool"
	
	if len(sys.argv) < 2:
		print "Usage: python dojosvn2git.py <repo dir> [<github account username>]"
		sys.exit(1)
	
	r = Repo(sys.argv[1], sys.argv[2] if len(sys.argv) > 2 else "")
	sys.exit(r.go())

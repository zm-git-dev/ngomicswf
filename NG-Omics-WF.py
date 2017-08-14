#!/usr/bin/env python
# =============================== NG-Omics-WF ==================================
#  _   _  _____         ____            _              __          ________ 
# | \ | |/ ____|       / __ \          (_)             \ \        / /  ____|
# |  \| | |  __ ______| |  | |_ __ ___  _  ___ ___ _____\ \  /\  / /| |__   
# | . ` | | |_ |______| |  | | '_ ` _ \| |/ __/ __|______\ \/  \/ / |  __|  
# | |\  | |__| |      | |__| | | | | | | | (__\__ \       \  /\  /  | |     
# |_| \_|\_____|       \____/|_| |_| |_|_|\___|___/        \/  \/   |_|     
#                                                                           
# =========================== Next Generation Omics data workflow tools ========
#
# Workflow tools for next generation genomics, metagenomics, RNA-seq 
# and other type of omics data analyiss, 
#
# Software originally developed since 2010 by Weizhong Li at UCSD
#                                               currently at JCVI
#
# https://github.com/weizhongli/ngomicswf       liwz@sdsc.edu
# ==============================================================================

import os
import sys
import re
import argparse 
from argparse import RawTextHelpFormatter
import math
import subprocess
import time
import logging
import textwrap
import imp
import collections
import xml.etree.ElementTree as ET

# import numpy as np
# import pandas as pd
# import matplotlib.pyplot as plt

__author__ = 'Weizhong Li'

############## Global variables
NGS_config = None
NGS_samples = []
NGS_sample_data = {}
NGS_opts = {}
pwd = os.path.abspath('.')
subset_flag = False
subset_jobs = []
qstat_xml_data = collections.defaultdict(dict)
job_list = collections.defaultdict(dict)  # as job_list[$t_job_id][$t_sample_id] = {}
execution_submitted = {}                  # number of submitted jobs (qsub) or threads (local sh)
############## END Global variables

def fatal_error(message, exit_code=1):
  print message
  exit(exit_code)


def read_parameters(args):
  """
  read option parameters from file or command line
  """
  if args.parameter_file:
    try:
      ##format example
      ##JobID_A opt0 opt1 opt2
      ##JobID_B opt0 opt1
      ##JobID_C opt0 opt1 opt2 opt3
      f = open(args.parameter_file, 'r')
      for line in f:
        if line[0] == '#':
          continue
        if not re.match('^w', line):
          continue
        ll = re.split('\s+', line.rstrip());
        NGS_opts[ ll[0] ] = ll[1:]
      f.close()
    except IOError:
      fatal_error('cannot open ' + args.parameter_file, exit_code=1)

  elif args.parameter_name:
    for line in re.split(',', args.parameter_name):
      ll = re.split(':', line);
      NGS_opts[ ll[0] ] = ll[1:]


def read_samples(args):
  """
  read sample and sample data from file or command line
  """
  if args.sample_file:
    try:
      f = open(args.sample_file, 'r')
      for line in f:
        if line[0] == '#':
          continue
        if not re.match('^\w', line):
          continue
        ll = re.split('\s+', line.rstrip());
        NGS_samples.append(ll[0]);
        NGS_sample_data[ ll[0] ] = ll[1:]
      f.close()
    except IOError:
      fatal_error('cannot open ' + args.sample_file, exit_code=1)

  elif args.sample_name:
    for line in re.split(',', args.sample_name):
      ll = re.split(':', line);
      NGS_samples.append(ll[0]);
      NGS_sample_data[ ll[0] ] = ll[1:]
  else:
    fatal_error('no input sample', exit_code=1)

  for sample in NGS_samples:
    if os.path.exists(sample):
      if os.path.isdir(sample):
        pass
      else:
        fatal_error('file exist: ' + sample, exit_code=1)
    else:
      if os.system("mkdir " + sample):
        fatal_error('can not mkdir: ' + sample, exit_code=1)

def task_level_jobs(NGS_config):
  '''
  according to dependancy, make level of jobs
  '''
  job_level = {}
  while True:
    change_flag = False
    for t_job_id in NGS_config.NGS_batch_jobs.keys():
      t_job = NGS_config.NGS_batch_jobs[t_job_id]
      t_injobs = t_job['injobs']

      if len(t_injobs) > 0:
        max_level_injob = 1
        for j in t_injobs:
          if not j in job_level.keys():
            continue
            if job_level[j] > max_level_injob:
              max_level_injob = job_level[j]
        if max_level_injob == 1:
          continue
        max_level_injob +=1  #### one more level 
        if (t_job_id in job_level.keys()) and (job_level[t_job_id] >= max_level_injob):
          continue
        job_level[t_job_id]=max_level_injob
        change_flag = 1
      else:
        if not t_job_id in job_level.keys():
          job_level[t_job_id]=1
          change_flag = True

    if not change_flag:
      break

  for t_job_id in NGS_config.NGS_batch_jobs.keys():
    NGS_config.NGS_batch_jobs[t_job_id]['job_level'] = job_level[t_job_id]
      
#### END task_level_jobs(NGS_config)


def add_subset_jobs_by_dependency(NGS_config):
  '''add dependant jobs'''
  while True:
    num_subset_jobs = len(subset_jobs)
    for t_job_id in subset_jobs:
      t_job = NGS_config.NGS_batch_jobs[t_job_id]
      for j in t_job['injobs']:
        if not (j in subset_jobs):
          subset_jobs.append(j)
    if num_subset_jobs == len(subset_jobs):
      break
#### END add_subset_jobs_by_dependency()

       
def make_job_list(NGS_config):
  '''
  make sh script for each job / sample
  '''

  verify_flag = False
  for t_job_id in NGS_config.NGS_batch_jobs:
    if subset_flag and not (t_job_id in subset_jobs):
      continue

    print t_job_id
    t_job = NGS_config.NGS_batch_jobs[ t_job_id ]
    t_execution = NGS_config.NGS_executions[ t_job["execution"] ]

    print t_job
    print t_execution
    
    pe_parameter = ''
    if t_execution[ 'type' ] == 'qsub-pe':
      t_cores_per_cmd  = t_job[ 'cores_per_cmd' ]
      pe_parameter = "#$ -pe orte " + str(t_cores_per_cmd)

    if t_job[ 'cores_per_cmd' ] > t_execution[ 'cores_per_node' ]:
      fatal_error('not enough cores ' + t_job, exit_code=1)
      ## -- write_log("$t_job_id needs $t_job->{\"cores_per_cmd\"} cores, but $t_job->{\"execution\"} only has $t_execution->{\"cores_per_node\"} cores");

    t_job[ 'cmds_per_node' ] = t_execution[ 'cores_per_node' ] / t_job[ 'cores_per_cmd' ]
    t_job[ 'nodes_total' ] = math.ceil( t_job[ 'no_parallel' ] / float(t_job[ 'cmds_per_node' ]))
 
    if t_job[ 'nodes_total' ] > t_execution[ 'number_nodes' ]:
      fatal_error('not enough nodes ' + t_job, exit_code=1)
      ## -- write_log("$t_job_id needs $t_job->{\"nodes_total\"} nodes, but $t_job->{\"execution\"} only has $t_execution->{\"number_nodes\"} nodes");

    CMD_opts = []
    if 'CMD_opts' in t_job.keys():  
      CMD_opts = t_job[ 'CMD_opts' ]
    if t_job_id in NGS_opts.keys():
      CMD_opts = NGS_opts[ t_job_id ]

    for t_sample_id in NGS_samples:
      t_command = t_job[ 'command' ]
      t_command = re.sub('\\\\SAMPLE', t_sample_id, t_command)
      t_command = re.sub('\\\\SELF'  , t_job_id, t_command)

      for i_data in range(0, len(NGS_sample_data[ t_sample_id ])):
        t_data = NGS_sample_data[ t_sample_id ][i_data]
        t_re = '\\\\DATA\.' + str(i_data)
        t_command = re.sub(t_re, t_data, t_command)

      t_injobs = []
      if 'injobs' in t_job.keys():
        t_injobs = t_job[ 'injobs' ]
        for i_data in range(0, len(t_job[ 'injobs' ])):
          t_data = t_job[ 'injobs' ][i_data]
          t_re = '\\\\INJOBS\.' + str(i_data)
          t_command = re.sub(t_re, t_data, t_command)

      for i_data in range(0, len(CMD_opts)):
        t_data = CMD_opts[i_data]
        t_re = '\\\\CMDOPTS\.' + str(i_data)
        t_command = re.sub(t_re, t_data, t_command)

      v_command = ''
      if 'non_zero_files' in t_job.keys():
        for t_data in t_job[ 'non_zero_files' ]:
          v_command = v_command + \
            'if ! [ -s {0}/{1} ]; then echo "zero size {2}/{3}"; exit; fi\n'.format(t_job_id, t_data, t_job_id, t_data)

      print '-' * 80
      print t_sample_id
      print t_command
      print v_command
    
      f_start    = pwd + '/' + t_sample_id + '/' + t_job_id + '/WF.start.date'
      f_complete = pwd + '/' + t_sample_id + '/' + t_job_id + '/WF.complete.date'
      f_cpu      = pwd + '/' + t_sample_id + '/' + t_job_id + '/WF.cpu'
      t_sh_file  = '{0}/WF-sh/{1}.{2}.sh'.format(pwd, t_job_id, t_sample_id)
      t_infiles = []
      if 'infiles' in t_job.keys():
        t_infiles = map(lambda x: t_sample_id + "/" + x, t_job[ 'infiles' ])
      job_list[ t_job_id ][ t_sample_id ] = {
        'sample_id'    : t_sample_id,
        'job_id'       : t_job_id,
        'status'       : 'wait',       #### status can be wait (input not ready), ready (input ready), submitted (submitted or running), completed
        'command'      : t_command,
        'sh_file'      : t_sh_file, 
        'infiles'      : t_infiles,
        'injobs'       : t_injobs,
        'start_file'   : f_start,
        'complete_file': f_complete,
        'cpu_file'     : f_cpu }

      if not os.path.exists( t_sh_file ):
        try:
          tsh = open(t_sh_file, 'w')
          tsh.write('''{0}
{1}

my_host=`hostname`
my_pid=$$
my_core={2}
my_queue={3}
my_time_start=`date +%s`

cd {4}/{5}
mkdir {6}
if ! [ -f {7} ]; then date +%s > {7};  fi
{8}
{9}
date +%s > {10}
my_time_end=`date +%s`;
my_time_spent=$((my_time_end-my_time_start))
echo "sample={5} job={6} host=$my_host pid=$my_pid queue=$my_queue cores=$my_core time_start=$my_time_start time_end=$my_time_end time_spent=$my_time_spent" >> {11}

'''.format(t_execution['template'], pe_parameter, t_job['cores_per_cmd'], t_job['execution'], pwd, t_sample_id, t_job_id, f_start, t_command, v_command, f_complete, f_cpu ))
          tsh.close()
        except IOError:
          fatal_error('cannot write to ' + job_list[ 't_job_id' ][ 't_sample_id' ][ 'sh_file' ], exit_code=1)
### END def make_job_list(NGS_config):


def task_log_cpu(NGS_config):
  '''
  my %cpu_info;
  foreach $t_job_id (keys %NGS_batch_jobs) {
    if ($subset_flag) {next unless ($subset_jobs{$t_job_id});} 
    my $t_job = $NGS_batch_jobs{$t_job_id};
    foreach $t_sample_id (@NGS_samples) {

      $cpu_info{$t_job_id}{$t_sample_id} = [$t_wall, $t_cpu];
    }
  }

  foreach $t_sample_id (@NGS_samples) {
    my $f_cpu = "$pwd/$t_sample_id/WF.cpu";
    open(CPUOUT, "> $f_cpu") || die "Can not open $f_cpu";
    print CPUOUT "#job_name\tCores\tWall(s)\tWall_time\tCPU(s)\tCPU_time\n";
    my $min_start = 1402092131 * 999999;
    my $max_end   = 0;
    my $sum_cpu   = 0;
    foreach $t_job_id (keys %NGS_batch_jobs) {
      if ($subset_flag) {next unless ($subset_jobs{$t_job_id});} 
      my $t_job = $NGS_batch_jobs{$t_job_id};
      my $t_core     = $t_job->{"cores_per_cmd"} * $t_job->{"no_parallel"};

      my $t_sample_job = $job_list{$t_job_id}{$t_sample_id};
      my $f_start    = $t_sample_job->{'start_file'};
      my $f_complete = $t_sample_job->{'complete_file'};
      my $f_cpu      = $t_sample_job->{'cpu_file'};
      my $t_start    = `cat $f_start`;    $t_start =~ s/\s//g; $min_start = $t_start if ($t_start < $min_start);
      my $t_end      = `cat $f_complete`; $t_end   =~ s/\s//g; $max_end   = $t_end   if ($t_end   > $max_end);
      my $t_wall     = int($t_end - $t_start);
         $t_wall     = 0 unless ($t_wall>0);

      my $t_cpu = 0;
      if (open(TCPU, $f_cpu)) {
        while($ll = <TCPU>) {
          chop($ll);
          if ($ll =~ /^(\d+)m(\d+)/) {
            $t_cpu += $1 * 60;
          }
        }
        close(TCPU);
      }
      $sum_cpu += $t_cpu;

      my $t_walls = time_str1($t_wall);
      my $t_cpus  = time_str1($t_cpu);
      print CPUOUT "$t_job_id\t$t_core\t$t_wall\t$t_walls\t$t_cpu\t$t_cpus\n";
    }
    my $t_wall = ($max_end - $min_start); $t_wall     = 0 unless ($t_wall>0);
    my $t_walls = time_str1($t_wall);
    my $sum_cpus= time_str1($sum_cpu);
    print CPUOUT "total\t-\t$t_wall\t$t_walls\t$sum_cpu\t$sum_cpus\n";
    close(CPUOUT);
  }
  '''
#### END def task_log_cpu():


def task_list_jobs(NGS_config):
  for t_job_id in NGS_config.NGS_batch_jobs.keys():
    t_job = NGS_config.NGS_batch_jobs[t_job_id]

    t_injobs = []
    if 'injobs' in t_job.keys():
      t_injobs  = t_job['injobs']
    print '{0}\tIn_jobs:[ {1} ]\tJob_level:{2}\n'.format(t_job_id, ','.join(t_injobs), t_job['job_level'] )


def task_snapshot(NGS_config):
  '''
  print job status
  '''

  '''
  if this_task:
    my $flag_qstat_xml_call = 0;
    foreach $t_job_id (keys %NGS_batch_jobs) {
      my $t_job = $NGS_batch_jobs{$t_job_id};
      my $t_execution = $NGS_executions{ $t_job->{"execution"} };
      my $exe_type = $t_execution->{type};
      $flag_qstat_xml_call = 1 if (($queue_system eq "SGE") and (($exe_type eq "qsub") or ($exe_type eq "qsub-pe")));
    }
    SGE_qstat_xml_query() if $flag_qstat_xml_call;

    foreach $t_sample_id (@NGS_samples) {
      foreach $t_job_id (keys %NGS_batch_jobs) {
        check_submitted_job($t_job_id, $t_sample_id);
      }
    }


  my $max_len_sample = 0;
  foreach $t_sample_id (@NGS_samples) {
    $max_len_sample = length($t_sample_id) if (length($t_sample_id) > $max_len_sample);
  }
  my $max_len_job = 0;
  foreach $t_job_id (@NGS_batch_jobs) {
    $max_len_job = length($t_job_id) if (length($t_job_id) > $max_len_job);
  }

  print <<EOD;
Job status: 
.\twait
-\tsubmitted
r\trunning  
+\tcompleted
!\terror
EOD

  for ($i=$max_len_job-1; $i>=0; $i--) {
    print ' 'x$max_len_sample, "\t";
    foreach $t_job_id (@NGS_batch_jobs) {
      print " ", ($i<length($t_job_id) ? substr(reverse($t_job_id), $i, 1):" ");
    }
    print "\n";
  }

  foreach $t_sample_id (@NGS_samples) {
    print "$t_sample_id\t";
    foreach $t_job_id (@NGS_batch_jobs) {
      my $t_sample_job = $job_list{$t_job_id}{$t_sample_id};
      my $status = $t_sample_job->{'status'};
      if    ($status eq "completed") { print " +";}
      elsif ($status eq "submitted") { print " -";}
      elsif ($status eq "running"  ) { print " r";}
      elsif ($status eq "wait"     ) { print " .";}
      elsif ($status eq "error"    ) { print " !";}
      else                           { print " _";}
    }
    print "\n";
  }
  '''
### def task_snapshot():


def task_delete_jobs():
  '''
sub task_delete_jobs {
  my $opt = shift;
  my ($i, $j, $k, $ll, $t_job_id, $t_sample_id);
  my ($mode, $c) = split(/:/, $opt);
  my $tmp_sh = "NGS-$$.sh";

  open(TMPSH, "> $tmp_sh") || die "can not write to file $tmp_sh";
  print TMPSH "#Please execute the following commands\n";
  foreach $t_sample_id (@NGS_samples) {
    my %job_to_delete_ids = ();
    if ($mode eq "jobids") {
       %job_to_delete_ids = map {$_, 1} split(/,/,$c);
    }
    elsif ($mode eq "run_after") {
      die "file $c doesn't exist!" unless (-e $c);
      foreach $t_job_id (keys %NGS_batch_jobs) {
        my $t_sample_job = $job_list{$t_job_id}{$t_sample_id};
        my $t_sh_file = $t_sample_job->{'sh_file'};
        my $t_sh_pid  = "$t_sh_file.pids";
        next unless (-e $t_sh_pid);   #### unless the job is submitted
        #$job_to_delete_ids{$t_job_id} = 1 if (file1_same_or_after_file2( $t_sample_job->{'start_file'} , $c));
        $job_to_delete_ids{$t_job_id} = 1 if (file1_same_or_after_file2( $t_sh_pid , $c));

      }
    }
    else {
      die "unknown option for deleting jobs: $opt";
    }

    # now %job_to_delete_ids are jobs need to be deleted
    # next find all jobs that depends on them, recrusively
    my $no_jobs_to_delete = scalar keys %job_to_delete_ids;
    while(1) {
      foreach $t_job_id (keys %NGS_batch_jobs) {
        my $t_sample_job = $job_list{$t_job_id}{$t_sample_id};
        my $t_sh_file = $t_sample_job->{'sh_file'};
        my $t_sh_pid  = "$t_sh_file.pids";
        next unless (-e $t_sh_pid);   #### unless the job is submitted
        my @t_injobs  = @{ $t_sample_job->{'injobs'} };
        foreach my $t_job_id_2 (@t_injobs) {
          $job_to_delete_ids{$t_job_id} = 1 if ($job_to_delete_ids{$t_job_id_2});
        }
      }
      last if ($no_jobs_to_delete == (scalar keys %job_to_delete_ids)); #### no more depending jobs
      $no_jobs_to_delete = scalar keys %job_to_delete_ids;
    }

    if ($no_jobs_to_delete) {
      print TMPSH "#jobs to be deleted for $t_sample_id: ", join(",", keys %job_to_delete_ids), "\n";
      print       "#jobs to be deleted for $t_sample_id: ", join(",", keys %job_to_delete_ids), "\n";
      foreach $t_job_id (keys %job_to_delete_ids) {
        my $t_sample_job = $job_list{$t_job_id}{$t_sample_id};
        my $t_sh_file = $t_sample_job->{'sh_file'};
        my $t_sh_pid  = "$t_sh_file.pids";
        print TMPSH "\\rm -rf $pwd/$t_sample_id/$t_job_id\n";
        print TMPSH "\\rm $t_sh_pid\n";        
        print TMPSH "\\rm $t_sh_file.*.std*\n";

        #### find the qsub ids to be deleted 
        my $qids = `cat $t_sh_pid`; $qids =~ s/\n/ /g; $qids =~ s/\s+/ /g;
        print TMPSH "qdel $qids\n";
      }
    }
  }
  close(TMPSH);
  print "The script is not delete the file, please run $tmp_sh to delete files!!!\n\n";
}
  '''
#### END def task_delete_jobs()


def SGE_qstat_xml_query():
  '''
  run qstat -f -xml and get xml tree
  '''

  qstat_xml_data = collections.defaultdict(dict)
  t_out = ''
  try:
    t_out  = subprocess.check_output(['qstat -f -xml'], shell=True)
  except:
    fatal_error("can not run qstat", exit_code=1)

  qstat_xml = ET.fromstring(t_out)
  qstat_xml_root = qstat_xml.getroot()
  for job_list in qstat_xml_root.iter('job_list'):
    job_id    = job_list.find('JB_job_number').text
    job_name  = job_list.find('JB_name').text
    job_state = job_list.find('state').text
    qstat_xml_data[job_id] = [job_name, job_state]

#### END def SGE_qstat_xml_query()


def print_job_status_summary(NGS_config):
  '''print jobs status'''
  job_status = ()
  job_total = 0

  for t_job_id in NGS_config.NGS_batch_jobs.keys():
    if subset_flag:
      if not subset_jobs[t_job_id]:
        continue
    for t_sample_id in NGS_samples:
      status = job_list[t_job_id][t_sample_id][status]
      job_status[status] +=1 ;
      job_total +=1 ;

  print 'total jobs: ', job_total 
  for i job_status.keys:
    print '{0}: {1}, '.format(i, job_status[i])
  print '\n'


def run_workflow(NGS_config):
  '''
  major look for workflow run
  '''
  queue_system = NGS_config.queue_system   #### default "SGE"
  sleep_time_min = 15
  sleep_time_max = 120
  sleep_time = sleep_time_min

  while 1:
    flag_job_done = True
    ########## reset execution_submitted to 0
    for i in NGS_config.NGS_executions.keys():
      execution_submitted[ i ] = False

    flag_qstat_xml_call = False
    for t_job_id in NGS_config.NGS_batch_jobs.keys():
      t_job = NGS_config.NGS_batch_jobs[t_job_id]
      t_execution = NGS_config.NGS_executions[ t_job['execution']]
      exe_type = t_execution['type']
      if (queue_system == SGE) and (exe_type in ['qsub','qsub-pe']):
        flag_qstat_xml_call = True

    if flag_qstat_xml_call:
      SGE_qstat_xml_query()

    ########## check and update job status for submitted jobs
    for t_job_id in NGS_config.NGS_batch_jobs.keys():
      t_job = NGS_config.NGS_batch_jobs[t_job_id]
      if subset_flag:
        if not subset_jobs[ t_job_id]:
          continue
      for t_sample_id in NGS_samples:
        t_sample_job = job_list[t_job_id][t_sample_id]
        if t_sample_job['status'] == 'completed':
          continue

        check_submitted_job(t_job_id, t_sample_id)
        if t_sample_job['status'] == 'completed':
          continue
        flag_job_done = False

    if flag_job_done:
      ##-- write_log("job completed!")
      break

    ########## check and update job status based on dependance 
    for t_job_id in NGS_config.NGS_batch_jobs.keys():
      t_job = NGS_config.NGS_batch_jobs[t_job_id]
      if subset_flag:
        if not subset_jobs[ t_job_id]:
          continue
      for t_sample_id in NGS_samples:
        t_sample_job = job_list[t_job_id][t_sample_id]
        if t_sample_job['status'] == 'wait':
          continue

        t_ready_flag = True
        for i in t_sample_job['infiles']:
          if os.path.exists(i) and os.path.getsize(i) > 0:
            continue
          t_ready_flag = False
          break

        for i in t_sample_job['injobs']:
          if job_list[i][t_sample_id]['status'] == 'completed':
            continue
          t_ready_flag = False
          break

        if t_ready_flag:
          t_sample_job['status'] = 'ready'
          ## -- write_log("$t_job_id,$t_sample_id: change status to ready");
    ########## END check and update job status based on dependance 

    ########## submit local sh jobs
    has_submitted_some_jobs = False
    for t_job_id in NGS_config.NGS_batch_jobs.keys():
      t_job = NGS_config.NGS_batch_jobs[t_job_id]
      if subset_flag:
        if not subset_jobs[ t_job_id]:
          continue
      t_execution = NGS_config.NGS_executions[ t_job['execution']]
      t_execution_id = t_job['execution']
      if t_execution['type'] != 'sh': 
        continue
      if execution_submitted[t_execution_id] >= t_execution['cores_per_node']:
        continue
      for t_sample_id in NGS_samples:
        t_sample_job = job_list[t_job_id][t_sample_id]
        if t_sample_job['status'] != 'ready':
          continue
        if (execution_submitted[t_execution_id] + t_job['cores_per_cmd'] * t_job['no_parallel']) > \
            t_execution['cores_per_node']: #### no enough available cores
          continue

        #### now submitting 
        pid_file = open( t_sample_job['sh_file'] + '.pids', 'w')
        for i in range(0, t_job['no_parallel']):
          p = subprocess.Popen(['/bin/bash', t_sample_job['sh_file']], shell=True)
          pid_file.write(str(p.pid))
        pid_file.close()
        t_sample_job['status'] = 'submitted'
        ## -- write_log("$t_job_id,$t_sample_id: change status to submitted");
        execution_submitted[ t_execution_id ] += t_job['cores_per_cmd'] * t_job['no_parallel'] 
        has_submitted_some_jobs = True
    ########## END submit local sh jobs

    ########## submit qsub-pe jobs, multiple jobs may share same node
    for t_job_id in NGS_config.NGS_batch_jobs.keys():
      t_job = NGS_config.NGS_batch_jobs[t_job_id]
      if subset_flag:
        if not subset_jobs[ t_job_id]:
          continue
      t_execution = NGS_config.NGS_executions[ t_job['execution']]
      t_execution_id = t_job['execution']

      if t_execution['type'] != 'qsub-pe':
        continue
      if execution_submitted[t_execution_id] >= t_execution['number_nodes']:
        continue
      t_cores_per_node = t_execution['cores_per_node']
      t_cores_per_cmd  = t_job['cores_per_cmd']
      t_cores_per_job  = t_cores_per_cmd * t_job['no_parallel']
      t_nodes_per_job  = t_cores_per_job / t_cores_per_node

      for t_sample_id in NGS_samples:
        t_sample_job = job_list[t_job_id][t_sample_id]
        if t_sample_job['status'] != 'ready':
          continue

        #### now submitting 
        pid_file = open( t_sample_job['sh_file'] + '.pids', 'w')
        for i in range(0, t_job['no_parallel']):
          t_stderr = t_sample_job['sh_file'] + '.' + str(i) + '.stderr'
          t_stdout = t_sample_job['sh_file'] + '.' + str(i) + '.stdout'

          command_line = 'qsub {0} {1} {2} {3} {4} {5} {6}'.format(t_execution['command_name_opt'], t_job_id,
                                                               t_execution['command_err_opt'], t_stderr, 
                                                               t_execution['command_out_opt'], t_stdout, t_sample_job['sh_file'])
          cmd = subprocess.check_output([command_line], shell=True)
          if re.search('\d+', cmd):
            pid = re.search('\d+', cmd).group(0)
            pid_file.write(pid)
          else:
            fatal_error('error submitting jobs')
          execution_submitted[t_execution_id] += t_nodes_per_job
          ## -- write_log("$t_sh_bundle submitted for sample $t_sample_id, qsubid $cmd");

        pid_file.close()
        t_sample_job['status'] = 'submitted'
        has_submitted_some_jobs = True
    ########## END submit qsub-pe jobs, multiple jobs may share same node
   
    ########## submit qsub jobs, job bundles disabled here, if need, check the original Perl script

    #### if has submitted some jobs, reset waiting time, otherwise double waiting time
    print_job_status_summary(NGS_config)
    if has_submitted_some_jobs:
      sleep_time = sleep_time_min
    else:
      sleep_time  *= 2
      if sleep_time > sleep_time_max:
        sleep_time = sleep_time_max
    ## --write_log("sleep $sleep_time seconds");
    time.sleep(sleep_time);
  #### END while 1:
#### END def run_workflow(NGS_config)


def check_pid(pid):        
  '''Check For the existence of a unix pid. '''
  try:
    os.kill(pid, 0)
  except OSError:
    return False
  else:
    return True


def check_any_pids(pids):
  '''Check For the existence of a list of unix pids. return True if any one exist'''
  for pid in pids:
    if check_pid(pid):
      return True
  return False


def check_any_qsub_pids(pids):
  '''Check For the existence of a list of qsub pids. return True if any one exist'''
  for pid in pids:
    if pid in qstat_xml_data.keys():
      return True
  return False


def validate_job_files(t_job_id, t_sample_id):
  '''return True if necessary file exist'''
  t_sample_job = job_list[t_job_id][t_sample_id]
  if not (os.path.exists(t_sample_job['start_file'])    and os.path.getsize(t_sample_job['start_file']) > 0):
    return False
  if not (os.path.exists(t_sample_job['complete_file']) and os.path.getsize(t_sample_job['complete_file']) > 0):
    return False
  if not (os.path.exists(t_sample_job['cpu_file'])      and os.path.getsize(t_sample_job['cpu_file']) > 0):
    return False
  return True


#### def check_submitted_job()
def check_submitted_job(NGS_config, t_job_id, t_sample_id):
  '''
  check submitted jobs by checking pids or qsub ids
  update job status from wait|ready -> submitted if pid file exit (in case of restart of this script)
  update job status from wait|ready|submitted -> completed if sh calls or qsub calls finished
  '''
  t_sample_job = job_list[t_job_id][t_sample_id]
  t_job = NGS_config.NGS_batch_jobs[t_job_id]
  t_execution = NGS_config.NGS_executions[ t_job['execution']]

  t_sh_pid = t_sample_job['sh_file'] + '.pids'
  if not os.path.exists(t_sh_pid):
    return

  status = t_sample_job['status']
  if ((status == 'wait') or (status == 'ready')):
    t_sample_job['status'] = 'submitted'
    ## write_log('t_job_id,t_sample_id: change status to submitted')

  pids = [] #### either pids, or qsub ids
  try:
    f = open(t_sh_pid, 'r')
    pids = f.readlines()
    f.close()
    pids = [x.strip() for x in pids]
  except IOError:
    fatal_error('cannot open ' + t_sh_pid, exit_code=1)

  if len(pids) == 0:
    fatal_error('empty file ' + t_sh_pid, exit_code=1)
  
  exe_type = t_execution['type']
  if (exe_type == 'sh'):
    if check_any_pids(pids):    #### still running
      execution_submitted[ t_job['execution'] ] += t_job['cores_per_cmd'] * t_job['no_parallel']
    elif validate_job_files(t_job_id, t_sample_id):                       #### job finished
      t_sample_job['status'] = 'completed'
      ## -- write_log('t_job_id,t_sample_id: change status to completed')
    else:
      t_sample_job['status'] = 'error'
      ## -- write_log('t_job_id,t_sample_id: change status to error')
    return
  elif ((exe_type == 'qsub') or (exe_type == 'qsub-pe')):
    if check_any_qsub_pids(pids):    #### still running
      pass
    elif validate_job_files(t_job_id, t_sample_id):                       #### job finished
      t_sample_job['status'] = 'completed'
      ## -- write_log('t_job_id,t_sample_id: change status to completed')
    else:
      t_sample_job['status'] = 'error'
      ## -- write_log('t_job_id,t_sample_id: change status to error')
  else:
    fatal_error('unknown execution type: '+ exe_type, exit_code=1)

#### END def check_submitted_job()






############################################################################################
# _______    ________  _________       ___________________   ________  .____       _________
# \      \  /  _____/ /   _____/       \__    ___/\_____  \  \_____  \ |    |     /   _____/
# /   |   \/   \  ___ \_____  \   ______ |    |    /   |   \  /   |   \|    |     \_____  \ 
#/    |    \    \_\  \/        \ /_____/ |    |   /    |    \/    |    \    |___  /        \
#\____|__  /\______  /_______  /         |____|   \_______  /\_______  /_______ \/_______  /
#        \/        \/        \/                           \/         \/        \/        \/ 
############################################################################################

if __name__ == "__main__":
  parser = argparse.ArgumentParser(formatter_class = RawTextHelpFormatter,
                                   description     = textwrap.dedent('''\

            ==================================================================
            Workflow tools for next generation genomics, metagenomics, RNA-seq
            and other type of omics data analyiss,
        
            Software originally developed since 2010 by Weizhong Li at UCSD
                                                          currently at JCVI
        
            http://weizhongli-lab.org/ngomicswf           liwz@sdsc.edu
            ==================================================================

   '''))

  parser.add_argument('-i', '--input',       help="workflow configration file, required", required=True)
  parser.add_argument('-s', '--sample_file', help='''
sample data file, required unless -S is present
File format example:
#Sample data file example, TAB or space delimited for following lines
Sample_ID1 sample_data_0 sample_data_1
Sample_ID2 sample_data_0 sample_data_1
Sample_ID3 sample_data_0 sample_data_1
  ''')
  parser.add_argument('-S', '--sample_name', help='''
sample data from command line, required unless -s is present
format:
Sample_ID1:sample_data_0:sample_data_0:sample_data_1,Sample_ID2:sample_data_0:sample_data_1
  ''')
  parser.add_argument('-t', '--parameter_file', help='''
replace default paramters in workflow configration file
File format example:
#parameter file example, TAB or space delimited for following lines
CMDOPT JobID_A:opt0:opt1:opt2
CMDOPT JobID_B:opt0:opt1
  ''')
  parser.add_argument('-T', '--parameter_name', help='''
parameter from command line
format:
JobID_A:opt0:opt1:opt2,JobID_B:opt0:opt1
  ''')
  parser.add_argument('-j', '--jobs', help='''run sub set of jobs, optional
the workflow will run all jobs by default.
to run sub set of jobs: -j qc or -j qc,fastqc
  ''')
  parser.add_argument('-J', '--task', help='''optional tasks
write-sh: write sh files and quite
log-cpu: gathering cpu time for each run for each sample
list-jobs: list jobs
snapshot: snapshot current job status
delete-jobs: delete jobs, must supply jobs delete syntax by option -Z
  e.g. -J delete-jobs -Z jobids:assembly,blast  ---delete assembly,blast and all jobs depends on them
       -J delete-jobs -Z run_after:filename     ---delete jobs that has start time (WF.start.date) after this file, and all depending jobs
  ''')
  parser.add_argument('-Z', '--second_parameter', help='secondary parameter used by other options, such as -J')
  parser.add_argument('-Q', '--queye', help='queue system, e.g. PBS, SGE', default='SGE')

  args = parser.parse_args()

  if (args.sample_file is None) and (args.sample_name is None) :
    parser.error('No sample file or sample name')

  NGS_config = imp.load_source('NGS_config', args.input)

  read_samples(args)
  print 'Samples'
  print NGS_samples
  print NGS_sample_data

  read_parameters(args)
  print 'Parameters'
  print NGS_opts

  if args.jobs:
    subset_flag = True
    subset_jobs = re.split(',', args.jobs)
    subset_jobs_by_dependency(NGS_config)
    print subset_jobs

  if not os.path.exists('WF-sh'):
    os.system('mkdir WF-sh')

  task_level_jobs(NGS_config)
  ## -- my @NGS_batch_jobs = sort {($NGS_batch_jobs{$a}->{'job_level'} <=> $NGS_batch_jobs{$b}->{'job_level'}) or ($a cmp $b)} keys %NGS_batch_jobs;

  make_job_list(NGS_config)

  ## single task
  if args.task:
    if args.task == 'log-cpu':
      task_log_cpu(NGS_config)
      exit(0)
    elif args.task == 'list-jobs':
      task_list_jobs(NGS_config)
      exit(0)
    elif args.task == 'snapshot':
      task_snapshot(NGS_config)
      exit(0)
    elif args.task == 'delete-jobs':
      task_delete_jobs(args.second_parameter)
      exit(0)
    elif args.task == 'write-sh':
      exit(0)
    else:
      fatal_error('undefined task' + args.task, exit_code=1)

################################################################################################
#  _____               _   _  _____  _____  _           _       _           _       _         
# |  __ \             | \ | |/ ____|/ ____|| |         | |     | |         (_)     | |        
# | |__) |   _ _ __   |  \| | |  __| (___  | |__   __ _| |_ ___| |__        _  ___ | |__  ___ 
# |  _  / | | | '_ \  | . ` | | |_ |\___ \ | '_ \ / _` | __/ __| '_ \      | |/ _ \| '_ \/ __|
# | | \ \ |_| | | | | | |\  | |__| |____) || |_) | (_| | || (__| | | |     | | (_) | |_) \__ \
# |_|  \_\__,_|_| |_| |_| \_|\_____|_____/ |_.__/ \__,_|\__\___|_| |_|     | |\___/|_.__/|___/
#                                      ______                      ______ _/ |                
#                                     |______|                    |______|__/                 
########## Run NGS_batch_jobs for each samples http://patorjk.com/software/taag
################################################################################################

  run_workflow(NGS_config)
  task_log_cpu(NGS_config)


"""
sub write_log {
  my @txt = @_;
  my $i;
  my $date = `date`; chop($date);
  foreach $i (@txt) {
    print LOG    "$date $i\n";
    print STDERR "$date $i\n";
  }
  print LOG    "\n";
  print STDERR "\n";
}

sub file1_same_or_after_file2 {
  my ($file1, $file2) = @_;

  # if not exist file1, assume it is in future, so it is newer
  if (not -e ($file1)) {return 0;}
  if (not -e ($file2)) {return 0;}

  my $mtime1 = (stat($file1))[9];
  my $mtime2 = (stat($file2))[9];

  return ( ($mtime1 >= $mtime2) ? 1 : 0);
}

sub time_str1 {
  my $s = shift;
  my $str = "";

  $str .= int($s/3600); $str .= "h"; $s = $s % 3600;
  $str .= int($s/60);   $str .= "m"; $s = $s % 60;
  $str .= $s;           $str .= "s";

  return $str;
}

"""

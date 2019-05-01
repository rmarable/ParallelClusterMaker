## Name:		README.md
## Author:	Rodney Marable <rodney.marable@gmail.com>
## Created On:	April 20, 2019
## Last Changed:	April 30, 2019
## Purpose:	Documentation for using the ParallelClusterMaker toolkit	

## Disclaimer

**This software is neither endorsed nor supported by Amazon Web Services.**

If you choose to use this software in production, please be forewarned that
bugs may still be present and unexpected (and undocumented) behavior may
be observed.  Use at your own risk! 

Please report any bugs, issues, or otherwise unexpected behavior to Rodney
Marable <rodney.marable@gmail.com> through the normal Github channels.

## ParallelClusterMaker Features

ParallelClusterMaker is a command line wrapper toolkit that makes it easier
to automate the creation and destruction of AWS ParallelCluster stacks.

The parent directory ParallelClusterMaker contains two subdirectories:

* **JumphostMaker** creates a dedicated free tier EC2 instance that can be
used to administer AWS ParallelCluster stacks.

* **ClusterMaker** builds and destroys AWS ParallelCluster stacks.  While it
can be run locally via OSX (and, in theory, Windows), the recommended practice
is to use JumphostMaker to first stand up a jumphost for controlling stacks.

ParallelClusterMaker supports the following features through the command line:

* User-configurable time-based cron-style cluster life cycle management i.e.
clusters will self-terminate when "--cluster_lifetime" has been exceeded.
The default is 30 days.

* Command line designation of dev, test, stage, and prod operating levels.

* Administrative control over allowed EC2 instance types for the admin and
compute node roles.

* Job scheduling with AWS Batch, Grid Engine, Torque, or Slurm.

* Separate instance types for the master and compute instances.

* User selection of "optimal" instances with AWS Batch as a scheduler.

* Identification of the cluster's owner, email address, and department using
an easily extendable tagging framework.

* Custom AMIs.

* Adjustable EC2 autoscaling configuration.

* Dynamic EC2 placement groups.

* Selective disabling of Intel HyperThreading (note: only on Amazon Linux 2).

* Default selection of spot instances with a pricing buffer to help prevent
instance termination due to spot price market fluctuations.

* Customization of Grid Engine parallel environments.

* Optional inclusion of a customizable HPC performance script repository to
enable immediate "quick and dirty" comparative testing of cluster stacks.

* Variable EBS volume sizes (up to 16 TB).

* EBS RAID volumes.

* Variable-sized shared EBS scratch mounted as /local_scratch on all cluster
instances.

* Creation of cluster-specific Amazon Elastic File System (EFS). 

* Encryption of EFS in transit and at rest.

* Creation of cluster-specific, custom-sized Amazon FSX for Lustre (FSxL)
file systems.

* Automounting of external NFS file systems from EMC, Netapp, Qumulo, WekaIO
Panzura, Nasuni, etc.

* Email notifications via SNS whenever significant cluster stack events are
recorded.

* Selective enablement of Ganglia for capturing cluster metrics.

* Operability in Turbot (https://www.turbot.com) environments.

* Inclusion of the Spack package manager (https://spack.readthedocs.io) for
lmod-style Linux software module support.

##########################################################
# Installation of ParallelClusterMaker for the Impatient #
##########################################################

The INSTALL.md document provides detailed guidance and instructions on how to
install ParallelClusterMaker using an EC2 jumphost (the preferred method) or
locally on OSX.

These instructions are for the impatient.  We strongly suggest that you review
the installation documentation carefully to avoid potentially costly and
time-consuming mistakes.

###############################################
# Building an Installation Environment on EC2 #
###############################################

To build a ParallelClusterMaker environment on EC2, refer to the guidelines
in the INSTALL.md file ("Building an installation environment on EC2").

Please note that this is the **recommended** way to leverage this toolkit.

* Install and enable a virtual Python environment with either virtualenv or
pyenv.

* Install the required Python libraries in ParallelClusterMaker/JumphostMaker.

* Run ParallelClusterMaker/Jumphost/make-pcluster-jumphost.py with appropriate
command line switches.

* Login to the jumphost.

* cd into the ParallelClusterMaker/ClusterMaker directory and start creating
new ParallelCluster stacks.  Please see the "Building New ParallelCluster
Stacks" section below for additional guidance.

###############################################
# Building an Installation Environment on OSX #
###############################################

It is strongly recommended that you refer to the guidelines outlined in
"Building an Installation Environment on OSX" in the INSTALL.md file  to
build a ParallelClusterMaker environment on OSX.

Please note that this is **not** the recommended method and may seriously 
damage your local OSX environment.

"Play at your own risk!" 
  -- Planet Patrol

* Install Homebrew.

* Install the following tools:
   * python-3.7 (with pip)
   * Docker
   * ansible
   * autoconf
   * automake
   * gcc
   * jq
   * libtool
   * make
   * nvm
   * node
   * readline
   * serverless

* Configure the AWS CLI.

* Install and activate a virtual Python environment using virtualenv or pyenv.

* Install all required Python libraries into the virtual environment using
the included requirements.txt file in each toolkit subdirectory once the
virtual Python environment is available.

* Install the Serverless Toolkit.

* In the AWS Management Console, apply a formal name to the VPC(s) within any
region you wish to deploy cluster stacks.

* cd into the ParallelClusterMaker/ClusterMaker directory and start building
new ParallelCluster stacks.  Please see below for additional guidance.

#######################################
# Building New ParallelCluster Stacks #
#######################################

After satisfying the installation and system requirements above, you can use
"make_cluster.py" to build new ParallelCluster stacks.  If you are building
from your local OSX environment, please remember to change into the proper
subdirectory beforehand (~/src/ParallelClusterMaker/ClusterMaker).  This
example will create a new cluster named rmarable-test01 in us-east-1a using
the toolkit defaults:

$ ./make_cluster.py -N test01 -O rmarable -E rodney.marable@gmail.com -A us-east-1a

A new deployment will typically take between 30-45 minutes to complete.  When
the new cluster becomes available, make an SSH connection to the IP address
of the master instance using the access_cluster.py command:

$ ./access_cluster.py -h
usage: access_cluster.py [-h] --cluster_name CLUSTER_NAME
                         [--prod_level PROD_LEVEL]

access_cluster.py: Provide quick SSH access to CFNCluster head nodes

optional arguments:
  -h, --help            show this help message and exit
  --cluster_name CLUSTER_NAME, -N CLUSTER_NAME
                        full name of the cluster (example: rmarable-stage02)
  --prod_level PROD_LEVEL, -P PROD_LEVEL
                        production state of the cluster (default = dev)

To access the cluster built above via SSH:

$ ./access_cluster.py -N rmarable-test01
Connecting to the head node of rmarable-test01 over ssh...
Last login: Sat Jul 21 23:13:35 2018 from 64.192.133.129

#####################################
# Using the HPC Performance Toolkit #
#####################################

ParallelClusterMaker provides an optional suite of performance tests that 
are included with new ParallelCluster stacks when the appropriate option
("--enable_hpc_performance_tests=true") is invoked at installation.  These
scripts live in ~/src/ParallelClusterMaker/ClusterMaker/performance.

Extensive documentation for these tests are included in the "performance"
subdirectory as README files.

At this time, only Grid Engine is natively supported.  However, support for
additional schedulers will be added in future releases.

Example usage:

$ cd src/ParallelClusterMaker/ClusterMaker/performance/rmarable-test01
$ qsub qsub-hashtest.10.sh

###########
# Ganglia #
###########

ParallelCluster uses Ganglia for monitoring cluster stacks.  You can find more
information about thsi software by visiting: http://ganglia.sourceforge.net/

Setting "--enable_ganglia=true" will install and enable Ganglia on the master
instance.  This option currently permits traffic to port 80 from the Internet,
so please consider this carefully if your clusters are operating in an
environment where security is a priority.  A future release will provide more
flexibility for controlling http access.

##################################################
# How to Manage EC2 Key Pairs for Cluster Stacks #
##################################################

ParallelCluster creates a unique EC2 key pair for each cluster stack and EC2
jumphost, storing the respective resulting PEM files in cluster_data_dir and
instance_data_dir.  By using the provided "access_cluster.py" (for clusters)
or "access_jumphost.py" scripts, HPC operators are abstracted from the need
to interact directly with a PEM key.

Shared clusters will need a mechanism to either distribute the PEM file in a
secure fashion.

The kill_cluster and kill_pcluster_jumphost scripts will delete the respective
EC2 key pairs as part of the cluster or jumphost destruction process.

#############################################
# Deleting a pcluster Stack or EC2 Jumphost #
#############################################

There are three ways to delete a cluster stack:

1. **Manual deletion**: Manually delete the Cloudformation template, EC2
security groups, EFS file systems, FSxL file systems, and custom EC2 IAM
roles associated with this ParallelCluster stack using the AWS Management
Console or the appropriate API calls.

Please note that manual stack deletion requires many mouse clicks (if using 
the AWS Management Console) or keystrokes (if using AWS API calls).  This
method is error-prone, time consuming, and thus is **NOT** a recommended best
practice.

2. **kill-pcluster.py**: Run the kill-pcluster.py script from your local
environment.  This tool can also be used to clean up artifacts from previous
builds or in situations where the the ParallelCluster stack has exceeded
cluster_lifetime and self-terminates.  To delete the cluster that was created
in the example above:

$ ./kill-pcluster.py -N test01 -O rmarable -A us-east-1a

Destroying a cluster will take between 5-10 minutes depending on the number
and type of instances deployed, whether EFS file systems are associated with
the cluster, etc.

3. **Wait for cluster_lifetime to take over**: Just hang out and wait.  All
ParallelCluster stacks are built with a default 30-day lifetime but this can
be changed by invoking "--cluster_lifetime=x:y:z" where x = days, y = hours,
and z = minutes to dictate exactly how long this stack should live.  In the
example below, rmarable-test01 will self-terminate in 12 hours without any
additional user (or DevOps) intervention:

$ ./make-pcluster.py -N test01 -O rmarable -E rodney.marable@gmail.com -A us-east-1a --cluster_lifetime="0:12:0"

A notification will be sent to cluster_owner_email when a stack destruction
event has occurred.  The user should then run the local kill-pcluster script
to remove any artifacts associated with the now-dead stack from the local
environment.

By default, any EFS or FSxL file systems associated with this cluster will
also be terminated along with the cluster stack.  For FXSxL, this behavior
can be overridden for FSxL by setting "delete_fsx" to "true" in the
delete_cluster.yml Ansible playbook.

To delete a pcluster jumphost, simply run the kill-pcluster-jumphost script
associated with the EC2 instance living in ParallelClusterMaker/Jumphost.

$ cd ParallelClusterMaker/JumphostMaker
$ ./kill-pcluster-jumphost.$INSTANCE_NAME.sh

##################
# Serial Nunbers #
##################

ParallelClusterMaker generates unique "serial numbers" for every EC2 jumphost
and ParallelCluster stack that are used for resource tagging and to associate
IAM roles and policies, EC2 instance profiles, and SNS topic names with each
individual entity.

The kill-cluster and kill-pcluster-jumphost scripts also use these unique
serial numbers to remove the aforementioned resources when a jumphost EC2
instance ParallelClusterMaker stack is deleted.  This makes it easier for
devops engineers to manage multiple HPC users in a single AWS account and
to run detailed usage reports for individual cluster stacks.

#######
# SNS #
#######

A notification will be sent to the cluster_owner when a stack construction
event has concluded via SNS.  If this is the first new stack you have created,
please make sure to "confirm" membership to the SNS topic that is created to
track major cluster events after it is emailed to cluster_owner_email.

The SNS topic associated with the jumphost or cluster stack will be deleted
by the kill-cluster or kill-pcluster-jumphost scripts.

#######################
# Local Scratch Space #
#######################

By default, all instances will have a /local_scratch directory that allows
the operator to leverage the local EBS volume as ephemeral scratch space.
This provides maximum flexibility as the cluster can utilize local SSD,
shared EBS, FSxL, EFS, or any storage combination thereof to support multiple
use cases.

The maximum available size of the /local_scratch disk is as follows:

{{ local_scratch }} = {{ ebs_volume_size }} - {{ Linux_OS + system_tools }}

Shared RAID EBS volumes are not currently supported by ParallelClusterMaker.
This may be addressed in a future release.  If greater performance from the
shared storage is required, the operator is encouraged to use EFS max_io or
FSxL instead.

#######
# EFS #
#######

EFS can be used as a shared storage option for ParallelCluster by setting
"--enable_efs=true."  Creating a new EFS file system and mount target will
add an extra 5-7 minutes to the overall cluster stack creation process.

/efs will be created and mounted by all instances in the new cluster stack.

This toolkit will build only one EFS file system which will be deleted along 
along with the cluster stack.  To override this behavior, set "--delete_efs"
to "false" when invoking kill_cluster.py.  Support for multiple EFS file
systems may be added in a future release.

To find more information about ParallelCluster's implementation of EFS, please
consult the official documentation:

https://aws-parallelcluster.readthedocs.io/en/latest/configuration.html#efs-section

Support for encryption and selecting max_io/general_purpose modes can be 
enabled by setting the appropriate command line switches to "true."

As of 4/20/2019, provisioning a 1024 MiB/sec file system costs approximately
$6,200/month.  For that reason, efs_throughput_mode has been completely
disabled to prevent unexpected and unpleasant surprises with the AWS bill.
This will be re-enabled as a future update.

#########################
# FSx for Lustre (FSxL) #
#########################

FSx for Lustre (FSxL) can be leveraged for scratch space on a ParallelCluster
stack by setting "--enable_fsx=true" and waiting an additional 5-7 minutes to
create the Lustre file system, mount point, and security group.

All cluster instances will mount the FSxL file system at /fsx.  The current
implementation only supports "ephemeral" scratch, that is, S3 hydration and
export is not supported.  This may change in a future release.

The **minimum** permitted file system size is 3600 GB (or 3.6 TB).  This is
the current default.  Larger file systems can be built by setting "--fsx_size"
to the desired value.

Please be advised that ParallelClusterMaker currently supports only one FSxL
mount per cluster and will only create FSxL mount targets in the cluster's
selected Availability Zone.  This may be changed in a future release.

By default, all FSxL file systems will be deleted along with the cluster
stack.  To override this behavior, change "--delete_fsx" to "false" when
invoking the kill_cluster.py script.

ParallelClusterMaker does **not** support FSxL on Ubuntu because installation
of the Lustre client process necessitates a reboot of all cluster instances,
which in turn breaks the ParallelCluster Cloudformation template deployment.
This may be revisited in a future release but for now, there are numerous 
checks in place to prevent the operator from setting "--enable_fsx=true" 
when "base_os=ubuntu1604".

#######################
# External NFS Access #
#######################

Support for external NFS access is configured in the "create_pcluster.yml"
and "delete_pcluster.yml" playbooks.

This example mounts an external file system called "storage.domain.com" onto
a ParallelClusterMaker-spawned stack:

"--enable_external_nfs=true" and "--external_nfs_server=storage.domain.com"

Please do **NOT** enable this feature without having a working external NFS
file system prepared to serve mount requests to this domain name!

A future release will provide more flexibility with mounting external NFS
file systems on new cluster stacks.

##############################################
# Suggested ParallelClusterMaker Invocations #
##############################################

Here are some ways that the ParallelClusterMaker toolkit can be used:

$ cd src/ParallelClusterMaker/ClusterMaker

* Create a cluster named "bb8" in us-east-2b that uses only shared EBS with 
Torque as the scheduler:

$ ./make-pcluster.py -A us-east-2b -E rodney.marable@gmail.com -O rmarable -N bb8 --scheduler=torque

* Create a cluster named "morpheus" in us-east-1a with an encrypted EFS file
system and support for TLS encryption using Slurm as the scheduler:

$ ./make-pcluster.py -A us-east-1a -E rodney.marable@gmail.com -O rmarable -N morpheus --enable_efs=true --efs_encryption=true --scheduler=slurm

* Create a cluster named "tombrady12goat" in us-west-2b with a 16 TB FSxL file
system using Grid Engine as the scheduler:

$ ./make-pcluster.py -A us-west-2b -E rodney.marable@gmail.com -O rmarable -N tombradygoat --enable_fsx=true --fsx_size=16384

* Create an AWS Batch environment named "terrordome" in us-east-2a that uses
only shared EBS and can scale up to 64 cores:

$ ./make-cluster.py -A us-east-2a -E rodney.marable@gmail.com -O rmarable -N creed --scheduler=awsbatch --desired_vcpus=64

* Create a super massive production cluster named "lukecage" with a maximum
of 3,072 compute cores in eu-west-1b that uses a 32 TB Lustre file system for
scratch and an EFS file system for shared storage, Amazon Linux 2 as the base
operating system, Grid Engine as the scheduler, system, and Ganglia for
cluster monitoring:

			    ###############
			    #   WARNING   #
			    ###############

	    DO **NOT** INVOKE THIS COMMAND TO BUILD THIS CLUSTER
	    UNLESS YOU ARE PREPARED FOR THE BILL THAT WILL ENSUE!

$ ./make-cluster.py -A eu-west-1b -E rodney.marable@gmail.com -O rmarable -N lukecage --base_os=alinux2 --enable_ganglia=true --master_instance_type=m5.2xlarge --compute_instance_type=r5.12xlarge --enable_fsx=true --fsx_size=32768 --enable_efs=true --prod_level=prod --max_queue_size=64

###########################
# Customizing Departments #
###########################

ParallelClusterMaker currently supports the following "departments" by using
the "--cluster_owner_department" switch:

research (default)
hpc
analytics
compchem
compbio
datasci
design
clinical
commercial
development
finance
infrastructure
manufacturing
operations
proteomics
qa
robotics
scicomp

########################
# Intel HyperThreading #
########################

Seeting "--hyperthreading=false" will disable Intel HyperThreading on Amazon
Linux following the guidance provided in this AWS blog posting:

http://tiny.amazon.com/1avqrokh6/awsamazblogdisa

**This flag currently has no effect with Ubuntu or CentOS.**

This feature will be extended to these operating systems in a future release.

###############
# Custom AMIs #
###############

ParallelClusterMaker supports custom AMIs through the "-custom_ami" switch,
provided the base_os is supported by ParallelCluster (currently CentOS 6/7,
Amazon Linux, and Ubuntu 16.04 LTS).  The base_os of the custom_ami must
also be supplied or the script will assume you are using Amazon Linux.

Ubuntu support for FSxL can be enabled by installing the required Debian
kernel packages, Lustre client, and Grub configuration per the public AWS
documentation available at:

https://docs.aws.amazon.com/fsx/latest/LustreGuide/install-lustre-client.html

An FSxL-enabled Ubuntu stack could then be built using this command line,
replacing the obvious with your own values:

$ ./make-cluster.py -N starscream -O rmarable -E rodney.marable@gmail.com -A us-west-2a --enable_fsx=true --custom_ami=ami-123456789abc --base_os=ubuntu1604

Error checking is performed to ensure that the custom AMI exists.

########################
# EC2 Placement Groups #
########################

EC2 placement groups can be enabled by setting "--placement_group=DYNAMIC".
ParallelCluster provides a mechanism for using an already-existing placement
group, but this feature is not currently supported by ParallelClusterMaker.

ParallelClusterMaker will place the master and compute instances into the
same placement group **if** the master and compute instance types are
identical.  If not, only the compute instances will be placed.

More information regarding EC2 placement groups can be found by visiting:

http://docs.aws.amazon.com/AWSEC2/latest/UserGuide/placement-groups.html

Per the AWS public documentation, please use this option with caution.

########################################
# HPC Software and Application Support #
########################################

ParallelClusterMaker automatically provides support for Spack, a package
manager that makes it easier to build and maintain multiple versions and
configurations of complex HPC software.

More information about Spack can be found by exploring these links:

* Spack project home page: https://spack.io/
* "The Spack Package Manager: Bringing Order to HPC Software Chaos" (Gamblin,
Legendre, Collette, et. al.): https://tgamblin.github.io/pubs/spack-sc15.pdfk

The version of Spack that is provided with these cluster stacks natively
supports Lmod, a package manager that for managing environment modules which 
most HPC engineers will be familiar with if they have ever worked extensivly
with onprem environments.

More information about Lmod can be found by exploring these links:

* https://github.com/TACC/Lmod
* https://sea.ucar.edu/sites/default/files/talk.pdf
* https://www.tacc.utexas.edu/research-development/tacc-projects/lmod

##############
# .gitignore #
##############

The included .gitignore excludes the directories containing the vars_files
and state data for the jumphost instances and ParallelCluster stacks created
by this toolkit.

If you wish to preserve these files within your own private or internal Git
repositories, please modify ParallelClusterMaker/.gitignore accordingly.

##################
# Reporting Bugs #
##################

Please report any bugs, issues, or otherwise unexpected behavior to Rodney
Marable <rodney.marable@gmail.com> through the normal Github channels.

Pull requests for additional functionality are always welcome.

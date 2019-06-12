# ParallelClusterMaker - HPC Cloud Automation

## License Information

Please refer to the LICENSE document included with this Open Source software for the specific terms and conditions that govern its use.

## Disclaimer

By using this Open Source software:

* You accept all potential risks involved with your use of this Open Source software.

* You agree that the author shall have no responsibility or liability for any losses or damages incurred in conjunction with your use of this Open Source Software.

* You acknowledge that bugs may still be present, unexpected behavior might be observed, and some features may not be completely documented.

**This Open Source software is authored by Rodney Marable in his individual capacity and is neither endorsed nor supported by Amazon Web Services.**

You cannot create cases with AWS Technical Support or engage AWS support engineers in public forums if you have any questions, problems, or issues using this Open Source software.

```
"Play at your own risk!"
 -- Planet Patrol
```

## About ParallelClusterMaker

ParallelClusterMaker is an Open Source command line wrapper toolkit that makes it easier to automate the creation and destruction of AWS ParallelCluster stacks. It is designed to enable scientists and engineers to leverage HPC in the cloud without requiring deep infrastructure knowledge and is a useful teaching tool for those looking to deepen their knowledge about HPC in general.

You can find more information about AWS ParallelCluster by visiting:

* Documentation: https://aws-parallelcluster.readthedocs.io/en/latest/index.html
* Github Repository: https://github.com/aws/aws-parallelcluster

The ParallelClusterMaker Github repository contains two project subdirectories:

* **JumphostMaker** creates a dedicated free tier EC2 instance that can be
used to build and maintain AWS ParallelCluster stacks.

JumphostMaker should be run locally on OSX or Linux.  Our recommended best practice is to use JumphostMaker to create a standalone EC2 instance (a.k.a. "jumphost") that can then be used to administer ParallelCluster stacks.

* **ClusterMaker** builds and destroys AWS ParallelCluster stacks.

ClusterMaker can also be run locally via OSX but the operator is strongly urged
to use JumphostMaker to first stand up a standalone EC2 instance (a.k.a. "jumphost") which can then be used to administer ParallelCluster stacks.

Please consult the EXAMPLES.md file for some suggestions on how to use ParallelClusterMaker's command line arguments to satisfy a variety of HPC use cases.

## ParallelClusterMaker Features

ParallelClusterMaker supports the following features through its command line interface:

* User-configurable time-based cron-style cluster life cycle management that
causes stacks to self-terminate when `--cluster_lifetime` has been exceeded.
The default is 30 days.

* Command line designation of dev, test, stage, and prod operating levels.

* Administrative control over the allowed EC2 instance types for the admin and compute node roles.

* Job scheduling with AWS Batch, Grid Engine, Torque, or Slurm.

* Separate selectable instance types for the master and compute instances.

* User selection of "optimal" instances when AWS Batch is selected as a scheduler.  Additionally, the user is free to select any of the EC2 instances that Batch supports for building computational environments.

* Identification of the cluster's owner, email address, and department using
an easily extendable tagging framework.  The cluster can also be associated
with a specific project identification tag.

* Custom AMIs.

* Adjustable EC2 autoscaling configuration.

* Dynamic EC2 placement groups.

* Selective disabling of Intel HyperThreading (note: this feature is currently available only on Amazon Linux).

* Default selection of EC2 Spot instances with a pricing buffer to help prevent
instance termination due to spot price market fluctuations.

* Customization of Grid Engine parallel environments.

* Optional inclusion of a customizable HPC performance script repository to
enable immediate "quick and dirty" comparative testing of traditional HPC schedulers (Grid Engine, Slurm, and Torque).  Support for AWS Batch will be provided in a future release.

* Variable EBS volume sizes (up to 16 TB).

* Variable-sized shared EBS scratch mounted as /local_scratch on all cluster instances.

* Creation of cluster-specific Amazon Elastic File System (EFS). 

* Encryption of EFS in transit and at rest.

* Creation of cluster-specific, custom-sized Amazon FSX for Lustre (FSxL)
file systems.  Caveat: the file size of any Lustre file system must be divisible by 3600.

* Support for hydrating and dehydrating the Lustre file system from pre-existing S3 buckets.

* Automounting of external NFS file systems from Vast, EMC, Netapp, Qumulo, WekaIO, Panzura, Nasuni, etc.

* Email notifications via SNS whenever ParallelClusterMaker is executed.

* Selective enablement of Ganglia for capturing cluster metrics.

* Operability in Turbot (https://www.turbot.com) environments.

* Inclusion of the Spack package manager (https://spack.readthedocs.io) for
lmod-style Linux software module support.

* Inclusion of a standard submission script for Grid Engine clusters that can
be customized for your specific use case.  Support for other schedulers will
be added in future releases.

## Installation of ParallelClusterMaker for the Impatient

Please read the INSTALL.md document for detailed guidance and instructions on how to install ParallelClusterMaker using an EC2 jumphost (the preferred method) or locally on OSX.

These instructions are provided for the impatient and/or lazy.

**It is strongly suggested that the reader carefully review the installation documentation (INSTALL.md) to avoid potentially costly and time-consuming mistakes.**

### Building an Installation Environment on EC2

To properly build a ParallelClusterMaker environment on EC2, please refer to the guidelines in the INSTALL.md file ("Building an installation environment on EC2").

```
Please note that this is the **recommended** way to leverage this toolkit.
```

* Install and enable a virtual Python environment with either virtualenv or
pyenv.

* Install the required Python libraries in ParallelClusterMaker/JumphostMaker.

* Run ParallelClusterMaker/Jumphost/make-pcluster-jumphost.py with appropriate
command line switches.

* Login to the jumphost.

* cd into the ParallelClusterMaker/ClusterMaker directory and start creating
new ParallelCluster stacks.  Please see the "Building New ParallelCluster
Stacks" section below for additional guidance.

### Building an Installation Environment on OSX

Please note that this is **not** the recommended method and may seriously 
damage your local OSX environment.

It is strongly recommended that you refer to the guidelines outlined in
"Building an Installation Environment on OSX" in the INSTALL.md file  to
build a ParallelClusterMaker environment on OSX.

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

* Ensure that the IAM account or role you are using to build ParallelCluster
stacks has permission to run the required API calls by either assigning it
admin rights or creating a custom role using this template as a source:

```ParallelClusterMaker/JumphostMaker/templates/ParallelClusterInstancePolicy.json_src```

* Install and activate a virtual Python environment using virtualenv or pyenv.

* Install all required Python libraries into the virtual environment using
the included requirements.txt file in each toolkit subdirectory once the
virtual Python environment is available.

* Install the Serverless Toolkit and its dependencies.

* In the AWS Management Console, apply a formal name to the VPC(s) within any
region you wish to deploy cluster stacks.

* cd into the ParallelClusterMaker/ClusterMaker directory and start building
new ParallelCluster stacks.  Please see below for additional guidance.

## Building New ParallelCluster Stacks

After satisfying the installation and system requirements outlined above, use
"make_cluster.py" to build new ParallelCluster stacks.  If building from a
local OSX environment, please remember to change into the proper
subdirectory beforehand (~/src/ParallelClusterMaker/ClusterMaker).  This
example will create a new cluster named rmarable-test01 in us-east-1a using
the toolkit defaults:

`$ ./make_cluster.py -N test01 -O rmarable -E rodney.marable@gmail.com -A us-east-1a`

A new deployment will typically take between 30-45 minutes to complete.  When
the new cluster becomes available, make an SSH connection to the IP address
of the master instance using the access_cluster.py command:

```
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
```

To access the cluster built above via SSH:

```
$ ./access_cluster.py -N rmarable-test01
Connecting to the head node of rmarable-test01 over ssh...
Last login: Sat Jul 21 23:13:35 2018 from 64.192.133.129
```

## Using the HPC Performance Toolkit

ParallelClusterMaker provides an optional suite of performance tests that 
are included with new ParallelCluster stacks when the appropriate option
(`--enable_hpc_performance_tests=true`) is invoked at installation.  These
scripts live in ~/src/ParallelClusterMaker/ClusterMaker/performance but come
with the following caveats that will be addressed in future releases:

- AWS Batch is not currently supported.

- Cluster submission scripts for Torque support are not provided.

Extensive documentation for these tests are included in the "performance"
subdirectory as README files.

This example submits 10 of the hashtest jobs to a Grid Engine cluster:

```
$ cd src/ParallelClusterMaker/ClusterMaker/performance/rmarable-test01
$ qsub qsub-hashtest.10.sh
```

## Ganglia #

ParallelCluster uses Ganglia for monitoring cluster stacks.  You can find more
information about thsi software by visiting: http://ganglia.sourceforge.net/

Setting `--enable_ganglia=true` will install and enable Ganglia on the master
instance.  This option currently permits traffic to port 80 from the Internet,
so please consider this carefully if your clusters are operating in an
environment where security is a priority.  A future release will provide more
flexibility for controlling http access.

## Managing EC2 Key Pairs for Cluster Stacks

ParallelCluster creates a unique EC2 key pair for each cluster stack and EC2
jumphost, storing the respective resulting PEM files in cluster_data_dir and
instance_data_dir.  By using the provided "access_cluster.py" (for clusters)
or "access_jumphost.py" scripts, HPC operators are abstracted from the need
to interact directly with a PEM key.

Shared clusters will need a mechanism to either distribute the PEM file in a
secure fashion.

The kill_cluster and kill_pcluster_jumphost scripts will delete the respective
EC2 key pairs as part of the cluster or jumphost destruction process.

## Deleting ParallelClusterMaker Resources

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
`cluster_lifetime` and self-terminates.  To delete the cluster that created
in the example above:

`$ ./kill-pcluster.py -N test01 -O rmarable -A us-east-1a`

Destroying a cluster will take between 5-10 minutes depending on the number
and type of instances deployed, whether EFS file systems are associated with
the cluster, etc.

3. **Wait for cluster_lifetime to take over**: Just hang out and wait.  All
ParallelCluster stacks are built with a default 30-day lifetime but this can
be changed by invoking `--cluster_lifetime=x:y:z` where x = days, y = hours,
and z = minutes to dictate exactly how long this stack should live.  In the
example below, rmarable-test01 will self-terminate in 12 hours without any
additional user (or DevOps) intervention:

```
$ ./make-pcluster.py -N test01 -O rmarable -E rodney.marable@gmail.com -A us-east-1a --cluster_lifetime="0:12:0"
```

A notification will be sent to cluster_owner_email over SNS when a cluster
stack termination event occurrs.  The user can then run kill-pcluster.py to
remove artifacts associated with the deleted stack from the local environment.

Any EFS or FSxL file systems associated with this cluster will also be terminated along with the cluster stack.

If the Lambda script is used to destroy the cluster when cluster_lifetime has
exceeded, the kill-cluster.py script should still be run to clean up any artifacts that still remain.

After the ParallelCluster stack has been deleted, the pcluster jumphost can
also be deleted by running the `kill-pcluster-jumphost.$INSTANCE_NAME.sh`
script found in your local ParallelClusterMaker/Jumphost directory, i.e. the
directory on your working computer from which the jumphost was originally
spawned.

```
$ cd ~/src/ParallelClusterMaker/JumphostMaker
$ ./kill-pcluster-jumphost.$INSTANCE_NAME.sh
```

## Cluster and Jumphost Serial Numbers

ParallelClusterMaker generates unique "serial numbers" for every EC2 jumphost
and ParallelCluster stack that are used for resource tagging and to associate
IAM roles and policies, EC2 instance profiles, and SNS topic names with each
individual entity.

The kill-cluster and kill-pcluster-jumphost scripts also use these unique
serial numbers to remove the aforementioned resources when a jumphost EC2
instance ParallelClusterMaker stack is deleted.  This makes it easier for
devops engineers to manage multiple HPC users in a single AWS account and
to run detailed usage reports for individual cluster stacks.

## SNS 

A notification will be sent to the cluster_owner when a stack construction
event has concluded via SNS.  If this is the first new stack you have created,
please make sure to "confirm" membership to the SNS topic that is created to
track major cluster events after it is emailed to cluster_owner_email.

The SNS topic associated with the jumphost or cluster stack will be deleted
by the kill-cluster or kill-pcluster-jumphost scripts.

## Local Scratch Space

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

## Elastic File System (EFS) Support

EFS can be used as a shared storage option for ParallelCluster by setting
`--enable_efs=true.`  Creating a new EFS file system and mount target will
add an extra 5-7 minutes to the overall cluster stack creation process.

/efs will be created and mounted by all instances in the new cluster stack.

This toolkit will build only one EFS file system which will be deleted along 
along with the cluster stack.  To override this behavior, set `--delete_efs`
to `false` when invoking kill_cluster.py.  Support for multiple EFS file
systems may be added in a future release.

To find more information about ParallelCluster's implementation of EFS, please
consult the official documentation:

https://aws-parallelcluster.readthedocs.io/en/latest/configuration.html#efs-section

Support for encryption and selecting between max_io and general_purpose modes can be enabled by setting the appropriate command line switches to `true`.

As of 4/20/2019, provisioning a 1024 MiB/sec file system costs approximately
$6,200/month.  For that reason, `efs_throughput_mode` has been completely
disabled to prevent unexpected and unpleasant surprises with the AWS bill.
This will be re-enabled as a future update.

## FSx for Lustre (FSxL) Support

FSx for Lustre (FSxL) can be leveraged for scratch space on a ParallelCluster
stack by setting `--enable_fsx=true.`  All cluster instances will mount the resulting FSxL file system at /fsx. 

The **minimum** permitted file system size is 3600 GB (or 3.6 TB).  This is
the current default.  Larger file systems can be built by setting `--fsx_size`
to the desired value in **increments of 3600 GB.**  The build process will abort
if presented with an incorrect value for fsx_size.

FSxL on Ubuntu is not currently supported by ParallelClusterMaker.

ParallelClusterMaker also supports hydration and dehyration of Lustre file systems using pre-existing S3 buckets as outlined in the AWS public documentation:

https://docs.aws.amazon.com/fsx/latest/LustreGuide/fsx-data-repositories.html

Please note that automatic Lustre dehydration at the time of the cluster's deletion is not supported.  This feature may be provided in a future release.

The FSx chunk size, import and export paths, and bucket names can all be configured through ParallelClusterMaker switches.  In the example below, a cluster called "louievega" will hydrate its 7.2 TB Lustre file system from s3://s3DataImportBucket and dehydrate to s3://s3DataExportBucket, using a chunk size of 5 GB:

```
$ ./make-pcluster.py -A us-west-2b -E rodney.marable@gmail.com -O rmarable -N louievega --enable_fsx=true --fsx_size=7200 --enable_fsx_hydration=true --fsx_s3_import_bucket=s3DataImportBucket --fsx_s3_export_bucket=s3DataExportBucket --fsx_chunk_size=5000
```

Please consult EXAMPLES.md for command line examples reflecting some other relevant use cases.

## Mounting External NFS Servers

Support for external NFS access is configured in the "create_pcluster.yml"
and "delete_pcluster.yml" playbooks.  

The cluster mount points are listed in a Jinja2 template file:

```
$ cat ClusterMaker/templates/external_nfs_mount_list.j2
################################################################################
# Name:		external_nfs_mount_list.{{ cluster_name }}.conf
# Author:	Rodney Marable <rodney.marable@gmail.com>
# Created On:	May 21, 2019
# Last Changed:	May 21, 2019
# Deployed On:	{{ lookup('pipe','date \"+%B %-d, %Y\"') }}
# Purpose:	List NFS external file systems to be mounted by postinstall.sh
# Notes:	Comment out lines that don't contain a file system mount point
################################################################################
#
#home
#departments
performance
pkg
#projects
scratch
#tools
```

This example would mount the file systems described in the aforementioned template from an external NFS file system called "storage.domain.com" onto all instances of a ParallelClusterMaker-spawned stack:

`--enable_external_nfs=true` and `--external_nfs_server=storage.domain.com`

The template can easily by customized for your environment by uncommenting any of the existing file systems or by adding others as needed.  Please do **NOT** enable this feature without having a working external NFS file system that is properly configured to share these file systems with cluster instances in the cloud.

## Intel HyperThreading

Seeting `--hyperthreading=false` will disable Intel HyperThreading on Amazon
Linux following the guidance provided in this AWS blog posting:

http://tiny.amazon.com/1avqrokh6/awsamazblogdisa

**This flag currently has no effect with Ubuntu or CentOS.**

This feature will be extended to these operating systems in a future release.

## Custom AMIs

ParallelClusterMaker supports custom AMIs through the `--custom_ami` switch,
provided the base_os is supported by ParallelCluster (currently CentOS 6/7,
Amazon Linux, and Ubuntu 14.04/16.04 LTS).  The base_os of the custom_ami must
be supplied with the initial make-cluster invocation, and of course, the custom
AMI must exist or the build will fail.

The recommended way to incorporate custom AMIs into a ParallelCluster stack
is to build a custom AMI with "pcluster createami" and use this subsequent 
image ID with the `custom_ami` switch. 

For example, Lustre support for Ubuntu can be enabled by building a AMI with
the required Debian kernel packages, Lustre client, and Grub configuration per
the public AWS documentation available at:

https://docs.aws.amazon.com/fsx/latest/LustreGuide/install-lustre-client.html

Assuming an AMI ID of "ami-123456789abc," a Lustre-enabled Ubuntu stack could
then be built with ParallelClusterMaker using this command:

```
$ ./make-cluster.py -N starscream -O rmarable -E rodney.marable@gmail.com -A us-west-2a --enable_fsx=true --custom_ami=ami-123456789abc --base_os=ubuntu1604
```
Similar use cases like encrypted EBS root volumes, custom Linux kernels, or
complex local application installations can be supported with this approach.

## EC2 Placement Groups

EC2 placement groups can be enabled by setting `--placement_group=DYNAMIC`.
ParallelCluster provides a mechanism for using an already-existing placement
group, but this feature is not currently supported by ParallelClusterMaker.

ParallelClusterMaker will place the master and compute instances into the
same placement group **if** the master and compute instance types are
identical.  If not, only the compute instances will be placed.

More information regarding EC2 placement groups can be found by visiting:

http://docs.aws.amazon.com/AWSEC2/latest/UserGuide/placement-groups.html

Per the AWS public documentation, please use this option with caution.

## HPC Software Packages and Application Support

ParallelClusterMaker automatically includes support for Spack, a package
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

## Standard Job Submission Scripts

ParallelClusterMaker includes a standard submission script for Grid Engine 
clusters in the ec2-user's home directory on the master instance that can
be easily customized for many other use cases includuing serial, MPI, and
task array jobs.

To submit jobs from EFS, FSxL, or external NFS file systems, simply copy 
`qsub_default_submission_script.sh` to the desired submission location and
edit as needed:

```
$ mkdir -p /fsx/scratch/scicomp/my_projects
$ vi /home/ec2-user/qsub_default_submission_script.sh
$ cp /home/ec2-user/qsub_default_submission_script.sh /fsx/scratch/scicomp/my_projects
$ qsub qsub_default_submission_script.sh
```

Subsequent releases will provide standard submission scripts for the other
schedulers supported by AWS ParallelCluster.

# .gitignore

The included .gitignore excludes the directories containing the vars_files
and state data for the jumphost instances and ParallelCluster stacks created
by this toolkit.

If you wish to preserve these files within your own private or internal Git
repositories, please modify ParallelClusterMaker/.gitignore accordingly.

# Tagging

All resources associated with traditional schedulers are automatically tagged
with the following keys:

ClusterID
ClusterSerialNumber
ClusterOwner
ClusterStackType
ClusterOwnerEmail
ClusterOwnerDepartment
Encryption
ProdLevel
ProjectID (if defined)
DEPLOYMENT_DATE

This includes EC2 instances, EFS and FSxL file systems, Cloudformation stacks,
EBS root volumes, SQS queues, etc.

Please note that since the default ParallelCluster IAM rule does not permit
EC2CreateTags, EC2DescribeTags, or EC2DeleteTags, EBS root volume tagging
may fail when building cluster stacks on OSX.  Build ParallelCluster stacks
with an EC2 jumphost to avoid this potential issue.

AWS Batch does not currently support post-creation tagging of managed compute
environments, so the default tags outlined above will not be visible with
this scheduler.  However, it is still possible to identify Batch environments
using the "Application" tag, which uses "parallelcluster-$CLUSTER_NAME" for
its naming $CLUSTER_NAME parameter.

```
$ aws batch describe-compute-environments | jq '. | select(.computeEnvironment[].computeResources.tags.Application == "parallelcluster-rmarable-batch01")'
```

In terms of cost tracking, per the AWS Batch public documentation:

```
Q. What is the pricing for AWS Batch?
There is no additional charge for AWS Batch.  You only pay for the AWS
Resources (e.g. EC2 Instances) you create to store and run your batch jobs.
```

## Identifying Cluster Resources by Department

ParallelClusterMaker currently supports the following "departments" by using
the `--cluster_owner_department` switch:

* research (default)
* hpc
* analytics
* compchem
* compbio
* datasci
* design
* clinical
* commercial
* development
* finance
* infrastructure
* manufacturing
* operations
* proteomics
* qa
* robotics
* scicomp

## Identifying Clusters with a Project ID

Operators can use the "--project_id" (or "-P") command line argument to to
tag all resources with belonging to a cluster stack or an EC2 jumphost with
this value.  Please refer to the examples below for adding "projectMayhem"
as a tag for which additional cost or consumption data could be generated:

JumphostMaker example:

```
$ ./make-pcluster-jumphost.py -N jumphost03 -O rmarable -E rodney.marable@gmail.com -A us-west-2b --project_id=projectMayhem --cluster_owner_department=compchem --scheduler=sge --ansible_verbosity=-vv
```

ClusterMaker example:

```
$ ./make-cluster.py --cluster_name batch019 --cluster_owner rmarable --cluster_owner_email=rodney.marable@gmail.com --cluster_owner_department=compbio --az=eu-west-1a --project_id=projectMayhem --scheduler awsbatch --desired_vcpus=50
```

# Shared Jumphosts

As of May 2019, permissions to build ParallelCluster stacks when using an EC2
jumphost are provided through a custom IAM EC2 instance profile that restricts
certain API calls to resources including `instance_owner` in their name.

This means multiple users cannot share a jumphost to launch clusters, i.e.
"--instance_owner" and "--cluster_owner" must match.

Shared jumphosts between multiple team members will be addressed in a future
release.  For now, operators are advised to maintain a single jumphost per
cluster_owner.

# Reporting Bugs & Requesting New Features

Please report any bugs, issues, or otherwise unexpected behavior to Rodney
Marable <rodney.marable@gmail.com> through the normal Github issue reporting channel for this repository:

https://github.com/rmarable/ParallelClusterMaker/issues

Pull requests providing additional functionality or bug fixes are always welcome:

https://github.com/rmarable/ParallelClusterMaker/pulls

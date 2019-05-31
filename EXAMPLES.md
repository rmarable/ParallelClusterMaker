# Suggested ParallelClusterMaker Invocations

Here are some suggested ways that the ParallelClusterMaker toolkit can be used to build ParallelCluster stacks for a wide variety of use cases.

The most inportant default options for ParallelClusterMaker are as follows:

base_os = alinux (Amazon Linux)
master_instance_type = c5.xlarge
compute_instance_type = c5.xlarge

The root EBS volume size for the compute and master instances is **250 GB.**

The default scheduler is SGE.

The cluster autoscaler will start out with 2 execute instances, flexing up to
10 total.

When using AWS Batch as the scheduler, the cluster uses a default "optimal"
mix of c4, m4, and r4 instance types that will flex out to 20 total cores.

cluster_lifetime is set to 30 days.  On day 31, the cluster will be terminated
along with any EFS and FSX file systems that were built with ParallelClusterMaker.

# Grid Engine Examples

* Create a cluster named "bbking" in us-east-1b that uses shared EBS for storage and Grid Engine as the scheduler.  All other values are ParallelClusterMaker defaults:

```
$ ./make-pcluster.py -A us-east-1b -E rodney.marable@gmail.com -O rmarable -N bb8 --scheduler=sge
```

* Create a cluster named "tombrady12goat" in us-west-2b with a 14.4 TB FSxL file
system using Grid Engine as the scheduler, r5.xlarge instances for compute, and a t3.xlarge instance for the master:

```
$ ./make-pcluster.py -A us-west-2b -E rodney.marable@gmail.com -O rmarable -N tombradygoat --enable_fsx=true --fsx_size=14400 --master_instance_type=t3.lxarge --compute_instance_type=r5.xlarge
```

* Create a test cluster named "tchalla" in us-west-2c that hydrates and dehydrates a 36.2 TB Lustre file system to and from s3_hpc_data_bucket/cluster_input_directory and cluster_output_directory with a chunk size of 5 GB.  The HPC performance tests are also included with Grid Engine as the scheduler using MPI.  Hyperthreading is disabled and the cluster will offer stats that are visible with Ganglia.

```
$ ./make-pcluster.py -A us-west-2c -O rmarable -E rodney.marable@gmail.com --enable_fsx=true --fsx_size=3600 --enable_fsx_hydration=true --s3_import_bucket=s3-hpc-data-bucket --s3_import_path=cluster-input --s3-export_bucket=s3-hpc-data-bucket --s3_export_path=cluster-output --fsx_chunk_size=5000 --enable_hpc_performance_tests --enable_sge_pe=mpi
```

##                                WARNING
##
##            DO **NOT** INVOKE THESE COMMANDS TO BUILD CLUSTERS
##          UNLESS YOU ARE PREPARED FOR THE BILL THAT WILL ENSUE!

* Create a super massive production cluster named "lukecage" with a maximum
of 3,072 compute cores built from m5.2xlarge instance in eu-west-1b that uses
a 36.2 TB Lustre file system for scratch, EFS for shared storage, Grid Engine
as the scheduler, Amazon Linux 2 as the base operating system, and Ganglia for
cluster monitoring:

```
$ ./make-cluster.py -A eu-west-1b -E rodney.marable@gmail.com -O rmarable -N lukecage --base_os=alinux2 --enable_ganglia=true --master_instance_type=m5.2xlarge --compute_instance_type=r5.12xlarge --enable_fsx=true --fsx_size=36000 --enable_efs=true --prod_level=prod --max_queue_size=64
```

* Create a Slurm cluster named "koolkeith" in eu-central-1a that will deploy
initially with four (4) c5d.2xlarge compute instances but can flex up to a
maximum of 125 instances, keeping at least the initial 4 instances alive and
available for accepting jobs at all times.  If no job is received by a node
for 30 minutes, it will spin down.  This cluster uses EFS, belongs to the
compbio department, and is assigned to project "polaroid."

```
$ ./make-pcluster.py -A eu-central-1a -E rodney.marable@gmail.com -O rmarable -N koolkeith --scheduler=slurm --initial_queue_size=4 --max_queue_size=125 --maintain-initial-size=true --scaledown_idletime=30 --owner_department=compbio --project_id=polaroid --enable_efs=true --compute_instance_type=c5d.2xlarge
```

* Create an AWS Batch environment called "winterfell" in eu-north-1b that
will deploy initially with 250 vcpus but can flex up to 1000 vcpus, keeping
a minimum of 25 vcpus active at all times.  This cluster uses EFS, belongs
to the Imaging team and will self-terminate after 3 days.  This is an example
of an HPC environment that might over the weekend to train an ML algorithm
against a very large repository of images.

$ ./make-pcluster.py -A eu-north-1b -E rodney.marable@gmail.com -O rmarable -N winterfell --scheduler=awsbatch --min_vcpus=25 --max_vcpus=1000 --desired_vcpus=250 --owner_department=imaging --cluster_lifetime=3:0:0 --enable_efs=true
# Slurm Examples

* Create a cluster named "morpheus" in us-east-1a with an encrypted EFS file
system and support for TLS encryption using Slurm as the scheduler:

```
$ ./make-pcluster.py -A us-east-1a -E rodney.marable@gmail.com -O rmarable -N morpheus --enable_efs=true --efs_encryption=true --scheduler=slurm
```

# AWS Batch Examples

* Create an AWS Batch environment named "terrordome" in us-east-2a that uses
shared EBS and can scale up to 64 cores:

```
$ ./make-cluster.py -A us-east-2a -E rodney.marable@gmail.com -O rmarable -N creed --scheduler=awsbatch --desired_vcpus=64
```

# Autoscaling Examples

* Create a Grid Engine cluster named "yoda" in us-west-2b that will maintain
a fixed size of three (3) r4.2xlarge compute instances using shared EBS.

```
$ ./make-pcluster.py -A us-east-1a -E rodney.marable@gmail.com -O rmarable -N yoda --scheduler=sge --initial_queue_size=3 --max_queue_size=3 --compute_instance_type=r4.2xlarge
```

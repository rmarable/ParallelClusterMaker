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

* Create a cluster named "bb8" in us-east-2b that uses shared EBS for storage and Grid Engine as the scheduler.  All other values are ParallelClusterMaker defaults:

```
$ ./make-pcluster.py -A us-east-2b -E rodney.marable@gmail.com -O rmarable -N bb8 --scheduler=sge
```

* Create a cluster named "tombrady12goat" in us-west-2b with a 14.4 TB FSxL file
system using Grid Engine as the scheduler, r5.xlarge instances for compute, and a t3.xlarge instance for the master:

```
$ ./make-pcluster.py -A us-west-2b -E rodney.marable@gmail.com -O rmarable -N tombradygoat --enable_fsx=true --fsx_size=14400 --master_instance_type=t3.lxarge --compute_instance_type=r5.xlarge
```

* Create a test cluster named "tchalla" in us-west-2b that hydrates and dehydrates a 36.2 TB Lustre file system to and from s3_hpc_data_bucket/cluster_input_directory and cluster_output_directory with a chunk size of 5 GB.  The HPC performance tests are also included with Grid Engine as the scheduler using MPI.  Hyperthreading is disabled and the cluster will offer stats that are visible with Ganglia.

```
$ ./make-pcluster.py -A us-west-2b -O rmarable -E rmarable@amazon.com --enable_fsx=true --fsx_size=3600 --enable_fsx_hydration=true --s3_import_bucket=s3-hpc-data-bucket --s3_import_path=cluster-input --s3-export_bucket=s3-hpc-data-bucket --s3_export_path=cluster-output --fsx_chunk_size=5000 --enable_hpc_performance_tests --enable_sge_pe=mpi
```

* Create a super massive production cluster named "lukecage" with a maximum
of 3,072 compute cores built from m5.2xlarge instance in eu-west-1b that uses
a 36.2 TB Lustre file system for scratch, EFS for shared storage, Grid Engine
as the scheduler, Amazon Linux 2 as the base operating system, and Ganglia for
cluster monitoring:

##                                WARNING
##
##            DO **NOT** INVOKE THIS COMMAND TO BUILD THIS CLUSTER
##            UNLESS YOU ARE PREPARED FOR THE BILL THAT WILL ENSUE!

```
$ ./make-cluster.py -A eu-west-1b -E rodney.marable@gmail.com -O rmarable -N lukecage --base_os=alinux2 --enable_ganglia=true --master_instance_type=m5.2xlarge --compute_instance_type=r5.12xlarge --enable_fsx=true --fsx_size=36000 --enable_efs=true --prod_level=prod --max_queue_size=64
```

# Slurm Examples

* Create a cluster named "morpheus" in us-east-1a with an encrypted EFS file
system and support for TLS encryption using Slurm as the scheduler:

```
$ ./make-pcluster.py -A us-east-1a -E rodney.marable@gmail.com -O rmarable -N morpheus --enable_efs=true --efs_encryption=true --scheduler=slurm
```

# AWS Batch Examples

* Create an AWS Batch environment named "terrordome" in us-east-2a that uses
only shared EBS and can scale up to 64 cores:

```
$ ./make-cluster.py -A us-east-2a -E rodney.marable@gmail.com -O rmarable -N creed --scheduler=awsbatch --desired_vcpus=64


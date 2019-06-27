# FAQ

---

Q: What version of ParallelCluster is currently supported?

A: ParallelClusterMaker strives to stay aligned with the most recent production release of ParallelCluster (currently aws-parallelcluster-2.4.0).

---

Q: What functionality does ParallelClusterMaker have that is above and beyond the built-in functionality of AWS ParallelCluster? 

A: Please consult the "ParallelClusterMaker Features" section in the README.md document for the specific functionality that ParallelClusterMaker provides as an augmentation of ParallelCluster.

---

Q: Is this essentially a CLI wrapper with some extra tools/utilities included?

A: Yes! ParallelClusterMaker is a wrapper that makes it easier to create, delete, and maintain ParallelCluster stacks.  The "extra" tools and utilities that are provided are detailed in the "ParallelClusterMaker Features" section of the README.md document.

---
Q: How does the "jumphost" differ from the cluster master instance?

A: The EC2 "jumphost" is just an easier way to launch ParallelCluster without having to configure anything on your local machine beyond the AWS CLI and Python-3.6 (or greater).  It is totally separate from the cluster master instance and does **not** otherwise participate in cluster administration in any fashion.

---

Q: If customers use ParallelClusterMaker, can they still manipulate their clusters with the pcluster utility in AWS ParallelCluster?

A: Yes! ParallelClusterMaker simply provides a mechanism to make it easier to build, destroy, and manage HPC clusters in AWS.  You can continue to interact with ParallelCluster stacks using the pcluster utility.

---

Q: Can I use my own Amazon Machine Image (AMI) with ParallelClusterMaker?

A: Yes! Use the "--custom_ami" switch to provide the amiId of an existing image that you want to build the cluster from.


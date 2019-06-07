## FAQ

Q: What functionality does ParallelClusterMaker have that is above and beyond the built-in functionality of ParallelCluster? 

A: Please consult the "ParallelClusterMaker Features" section in the README.md document.

---

Q: Is this essentially a CLI wrapper with some extra tools/utilities included?

A: Yes! ParallelClusterMaker is a wrapper that makes it easier to create, delete, and maintain ParallelCluster stacks.  The "extra" tools and utilities that are provided are detailed in the "ParallelClusterMaker Features" section of the README.md document.

---
Q: How is the jump host different from the master node?

A: The jumphost is just an easier way to launch ParallelCluster without having to configure anything on your local machine beyond AWS CLI.  It is totally separate from the cluster master instance and does not participate in cluster administration in any fashion.

---

Q: If customers use ParallelClusterMaker, can they still manipulate their clusters with the pcluster utility in ParallelCluster?

A: Yes, ParallelClusterMaker just provides a mechanism to make it easier to build, destroy, and manage HPC clusters in AWS.


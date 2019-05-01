################################################################################
# Name:		create_ec2_keypair.sh
# Author:	Rodney Marable <rodney.marable@gmail.com>
# Created On:	April 16, 2019
# Last Changed: April 16, 2019
# Purpose:	Build new AWS EC2 key pairs
################################################################################

#!/bin/sh

# Get the name of the new ec2_keypair and the region.
# We don't do any error checking so user should caveat emptor.

read -p 'Enter the region (default = us-east-1): ' region
if [ -z $region ]
then
	region="us-east-1"
fi
echo "	Deployment region = $region"
read -p 'Enter the new ec2_keypair name (default = pcluster_jumphost_ec2_keypair): ' new_ec2_keypair
if [ -z $new_ec2_keypair ]
then
	new_ec2_keypair="pcluster_jumphost_ec2_keypair_${region}"
else
	new_ec2_keypair="$new_ec2_keypair_${region}"
fi
echo "	New ec2_keypair = $new_ec2_keypair"

# Look for an existing key and prompt the user to cancel if one if located.
# Otherwise create the key and preserve the resulting PEM file.

if [ -f $new_ec2_keypair.pem ]
then
	echo ""
	echo "***WARNING***"
	echo "$new_ec2_keypair.pem already seems to exist locally."
	echo ""
	read -p 'Type YES to confirm deletion of this existing key: ' confirm
	if [[ $confirm == "yes" || $confirm == "YES" ]]
	then
		aws --region $region ec2 delete-key-pair --key-name $new_ec2_keypair
		chmod 0700 $new_ec2_keypair.pem
		rm $new_ec2_keypair.pem
	else
		echo "Aborting new key creation..."
		exit 1
	fi
fi
echo ""
aws --region $region ec2 create-key-pair --key-name $new_ec2_keypair --query 'KeyMaterial' --output text > $new_ec2_keypair.pem
chmod 0600 $new_ec2_keypair.pem
echo "Built $new_ec2_keypair.pem successfully in region $region."
echo "Exiting..."
exit 0

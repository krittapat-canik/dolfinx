#!/bin/sh

# This script prepares and creates a .tar.gz package of
# the source tree. This should really be done with 'make dist'
# but until we get that working, this script does the job.

# Get the version number
VERSION=`cat ./configure.in | grep 'VERSION=' | cut -d'=' -f2`
echo 'Version number is '$VERSION

# Create the directory
DISTDIR=dolfin-$VERSION
echo 'Creating directory '$DISTDIR
rm -rf $DISTDIR
mkdir $DISTDIR

# Make clean
echo 'Making clean'
make clean > /dev/null

# Copy files
FILES=`ls | grep -v $DISTDIR`
echo 'Copying files'
cp -R $FILES $DISTDIR

# Remove unecessary files
REMOVE1=`find $DISTDIR | grep CVS`
REMOVE2=`find $DISTDIR -name '*~'`
REMOVE3=`find $DISTDIR -name '*.a'`
REMOVE4=$DISTDIR'/config.cache '$DISTDIR'/config.status '$DISTDIR'/config.log'
REMOVE5=`find $DISTDIR | grep '\.deps'`
REMOVE=$REMOVE1' '$REMOVE2' '$REMOVE3' '$REMOVE4' '$REMOVE5
echo 'Removing unecessary files'
rm -rf $REMOVE

# Create the archive
TARBALL='dolfin-'$VERSION'.tar.gz'
echo 'Creating arhive: '$TARBALL
tar zcf $TARBALL $DISTDIR

# Remove the directory
echo 'Removing temporary directory'
rm -rf $DISTDIR

# Unpack the tarball
echo 'Unpacking tarball to check if it compiles'
tar zxf $TARBALL

# Compile
echo 'Compiling in directory '$DISTDIR' (see make-'$VERSION'.log)'
MAKELOG='../make-'$VERSION'.log'
cd $DISTDIR
rm -f $MAKELOG
./configure >> $MAKELOG
make >& $MAKELOG

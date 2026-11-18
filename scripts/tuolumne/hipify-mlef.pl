#!/usr/bin/env perl
# Converts a copy of libMLEF from CUDA (runtime, cuBLAS, dense cuSOLVER) to HIP (runtime,
# hipBLAS, hipSOLVER's hipsolverDn compatibility API, which keeps cuSOLVER's signatures).
# libMLEF uses only the names below; anything else CUDA-specific is reported so the build
# stops instead of compiling half-converted code. Edits the files in place.
#
# Usage: hipify-mlef.pl FILE...
use strict;
use warnings;

my @includes = (
    [ qr{<cuda_runtime\.h>}, '<hip/hip_runtime.h>' ],
    [ qr{<cublas_v2\.h>},    '<hipblas/hipblas.h>' ],
    [ qr{<cusolverDn\.h>},   '<hipsolver/hipsolver.h>' ],
);

my %runtime = map { $_ => 1 } qw(
    Malloc Free Memcpy Memset MemcpyHostToDevice MemcpyDeviceToHost MemcpyDeviceToDevice
    Success Error_t GetErrorString GetLastError DeviceSynchronize);

my %blas = map { $_ => 1 } qw(
    Create Destroy Handle_t Status_t Dgemm Dgemv Dnrm2 Ddgmm Daxpy Dscal Dger);

# libMLEF passes cuBLAS's fill mode to cuSOLVER, which takes it in CUDA; hipSOLVER has its
# own type. No cuBLAS call in libMLEF takes a fill mode, so these all become hipSOLVER's.
my %fixed = (
    'cublasFillMode_t'         => 'hipsolverFillMode_t',
    'CUBLAS_FILL_MODE_UPPER'   => 'HIPSOLVER_FILL_MODE_UPPER',
    'CUBLAS_FILL_MODE_LOWER'   => 'HIPSOLVER_FILL_MODE_LOWER',
    'cusolverStatus_t'         => 'hipsolverStatus_t',
    'cusolverEigMode_t'        => 'hipsolverEigMode_t',
);

# libMLEF's own names (error helpers, a handle variable) that only look like CUDA ones.
my %own = map { $_ => 1 } qw(cudaCheck cublasCheck cusolverCheck cudaOK cublas);

my $leftover = 0;
for my $file (@ARGV) {
    open(my $in, '<', $file) or die "$file: $!";
    my $text = do { local $/; <$in> };
    close $in;

    for my $inc (@includes) {
        $text =~ s/#include\s+$inc->[0]/#include $inc->[1]/g;
    }
    for my $name (keys %fixed) {
        $text =~ s/\b\Q$name\E\b/$fixed{$name}/g;
    }
    $text =~ s/\bcuda(\w+)\b/exists $runtime{$1} ? "hip$1" : "cuda$1"/ge;
    $text =~ s/\bcublas(\w+)\b/exists $blas{$1} ? "hipblas$1" : "cublas$1"/ge;
    $text =~ s/\bCUBLAS_((?:OP|SIDE|STATUS)_\w+)\b/HIPBLAS_$1/g;
    $text =~ s/\bcusolverDn(\w*)\b/hipsolverDn$1/g;
    $text =~ s/\bCUSOLVER_(\w+)\b/HIPSOLVER_$1/g;

    # Report CUDA names left outside comments and strings.
    my $code = $text;
    $code =~ s{/\*.*?\*/}{}gs;
    $code =~ s{//[^\n]*}{}g;
    $code =~ s{"(?:\\.|[^"\\])*"}{""}g;
    my %seen;
    for my $name ($code =~ /\b((?:cuda|cublas|cusolver|CUBLAS_|CUSOLVER_)\w*)/g) {
        next if $own{$name} || $seen{$name}++;
        print STDERR "hipify-mlef: $file: unconverted $name\n";
        $leftover = 1;
    }

    open(my $out, '>', $file) or die "$file: $!";
    print $out $text;
    close $out;
}
exit $leftover;

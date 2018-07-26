#!/usr/bin/perl -w
## ==============================================================================
## Automated annotation tools
##
## program written by
##                                      Weizhong Li, UCSD
##                                      liwz@sdsc.edu
##                                      http://weizhong-lab.ucsd.edu
## ==============================================================================

my $script_name = $0;
my $script_dir = $0;
   $script_dir =~ s/[^\/]+$//;
   $script_dir = "./" unless ($script_dir);
require "$script_dir/ann_local.pl";

# copied from ann_ORF_taxon_func.pl, this script takes pre-binned
# assembly as ground true
#
# Based on alignments between ORFs and reference DB. e.g. kegg
# generated by cd-hit-2d and blast, and cluster info of reference DB,
# and ORF-scaffold membership
# annotate ORFs taxon and function, satisfy that ORFs belong to the same 
# scaffold get same of consistent taxon
use Getopt::Std;
getopts("i:r:a:o:e:d:s:t:s:x:p:d:X:",\%opts);
die usage() unless ($opts{i} and $opts{r} and $opts{a} and $opts{o} and $opts{t} and $opts{s} and $opts{x} and $opts{p});

my $bl_file      = $opts{i}; #### blast alignment file in m8 format
my $clstr_ref    = $opts{r}; #### cluster info
my $ORF_file     = $opts{a}; #### old ORF
my $taxon_file   = $opts{t}; #### taxon file, created by ~/git/ngomicswf/NGS-tools/taxon_print_tid_rank_table.pl
my $taxon_file2  = $opts{x}; #### taxon file, created by ~/git/ngomicswf/NGS-tools/taxon_print_tid_rank_table.pl, for prebin taxids
my $output       = $opts{o}; #### output ORF file
my $output_ann   = "$output-ann.txt"; #### output annotation
my $output_tax   = "$output-tax.txt"; #### output tax 
my $output_log   = "$output-ann.log";
my $output_sca   = $opts{p};
my $cutoff_e     = $opts{e}; 
   $cutoff_e     = 1e-6 unless defined($cutoff_e);
my $assembly_bin = $opts{s};
my $depth_file   = $opts{d}; ### depth of coverage of assembly
my $contaminant_file = $opts{X};
my $output_contaminant = "";


my ($i, $j, $k, $ll, $cmd);

my %contaminant_tids = ();
if ($contaminant_file) {
  $output_contaminant  = "$output_sca.contaminant";
  open(TMP, $contaminant_file) || die "can not open $contaminant_file";
  while($ll=<TMP>) {
    chop($ll);
    next if ($ll =~ /^#/);
    my @lls = split(/\s+/, $ll);
    $contaminant_tids{$lls[0]} = 1;
  }
  close(TMP);
}

my %sid_2_depth = ();
open(TMP, $depth_file) || die "can not open $depth_file";
while($ll=<TMP>){
  next if ($ll =~ /^#/);
  chop($ll);
  my ($sid, $x) = split(/\t/,$ll);
  $sid_2_depth{$sid} = $x;
}
close(TMP);

my $taxon_format = <<EOD;
Col.0   #taxid  511145
Col.1   rank    toprank
Col.2   name    Escherichia coli str. K-12 substr. MG1655
Col.3   superkingdom    Bacteria
Col.4   superkingdom_ti 2
Col.5   kingdom \\N
Col.6   kingdom_ti      \\N
Col.7   phylum  Proteobacteria
Col.8   phylum_ti       1224
Col.9   class   Gammaproteobacteria
Col.10  class_ti        1236
Col.11  order   Enterobacterales
Col.12  order_ti        91347
Col.13  family  Enterobacteriaceae
Col.14  family_ti       543
Col.15  genus   Escherichia
Col.16  genus_ti        561
Col.17  species Escherichia coli
Col.18  species_ti      562
Col.19  toprank Escherichia coli str. K-12 substr. MG1655
Col.20  toprank_ti      511145
EOD

my %taxon_info = ();
open(TMP, $taxon_file) || die "can not open $taxon_file";
while($ll=<TMP>) {
  chop($ll);
  next if ($ll =~ /^#/); 
  my ($tid, $rank, @lls) = split(/\t/, $ll);
  next unless ($rank eq "toprank");
  $taxon_info{$tid} = [@lls];
}
close(TMP);

open(TMP, $taxon_file2) || die "can not open $taxon_file2";
while($ll=<TMP>) {
  chop($ll);
  next if ($ll =~ /^#/);
  my ($tid, $rank, @lls) = split(/\t/, $ll);
  next unless ($rank eq "toprank");
  $taxon_info{$tid} = [@lls]; #### overwrite existing tid info
}
close(TMP);


my %orf_2_scaffold = ();
my %orf_info = (); #### start, end
my %scaffold_member_orfs = ();
my %scaffold_orf_count = ();
open(TMP, $ORF_file) || die "can not open $ORF_file";
while($ll=<TMP>) {
  if ($ll =~ /^>(\S+)/) {
    my $orf_id = $1;
    $orf_info{$orf_id} = join("\t", ORF_info($ll)) ;
    my $sid = $orf_id;
    if    ($sid =~  /_\d+$/) { $sid =~  s/_\d+$//; } #### prodigal
    elsif ($sid =~ /\.\d+$/) { $sid =~ s/\.\d+$//; } #### metagene

    $orf_2_scaffold{$orf_id} = $sid;
    if (not defined( $scaffold_member_orfs{$sid} )) {
      $scaffold_member_orfs{$sid} = [];
    } 
    push(@{$scaffold_member_orfs{$sid}}, $orf_id);
  }
} 
foreach $sid (keys %scaffold_member_orfs) {
  $scaffold_orf_count{$sid} = scalar @{$scaffold_member_orfs{$sid}};
}
close(TMP);
my @all_sids = keys %scaffold_member_orfs;
   @all_sids = sort { $scaffold_orf_count{$b} <=> $scaffold_orf_count{$a} } @all_sids;

my %ref_ids = ();
my %orf_2_hit = ();
$last_ORF = "";
if (-d $bl_file) {
 open(TMP, "cat $bl_file/* |") || die "can not open $bl_file";
}
elsif (-e $bl_file) {
  open(TMP, $bl_file) || die "can not open $bl_file";
}

    my $output_looks_like = <<EOD; 
#query                          subject         %       alnln   mis     gap     q_b     q_e     s_b     s_e     expect  bits
#0                              1               2       3       4       5       6       7       8       9       10      11
mHE-SRS012902|scaffold|86.16    gnl|CDD|226997  47.62   42      17      2       164     201     210     250     5e-04   37.6
mHE-SRS012902|scaffold|109.23   gnl|CDD|225183  47.46   236     122     1       1       236     475     708     1e-92    284
mHE-SRS012902|scaffold|109.23   gnl|CDD|224055  44.35   239     130     2       1       239     332     567     2e-84    259
mHE-SRS012902|scaffold|109.23   gnl|CDD|227321  39.50   238     140     3       1       238     324     557     9e-69    218
EOD

while($ll=<TMP>) {
  #ser:SERP1011|ti|176279|KO||len|10203
  chop($ll);
  my @lls = split(/\t/, $ll);
  my $orf_id = $lls[0];
  next unless ($lls[10] <= $cutoff_e);
  next if ($orf_id eq $last_ORF); #### only top hit

  my $rid = $lls[1];
  $ref_ids{ $rid } = 1;
  my $iden = $lls[2];
  my $alnln= $lls[3];  
  my $ref_len = $alnln;
  if ($rid =~ /len\|(\d+)/) {
    $ref_len = $1;
  }
  my $frac = int($alnln / $ref_len * 10000) / 10000;
  $orf_2_hit{$orf_id} = [$rid, $iden, $alnln, $frac];

  $last_ORF = $orf_id;
}
close(TMP);

my %ref_2_taxids = ();
my %ref_2_KO = ();
my %ref_2_ann = ();
open(TMP, $clstr_ref) || die "can not open $clstr_ref";
while($ll=<TMP>){
  if ($ll =~ /^>/) {
    chop($ll);
    my ($rid, $no1, $no_taxid, $KO, $des) = split(/\t/, substr($ll, 1));
    next unless ($ref_ids{$rid});
    $des = $KO unless ($des);

    $ref_2_ann{$rid} = $des;
    $ref_2_taxids{$rid} = [];
    if ($KO =~ /KO\|(\w+)/) {
      $ref_2_KO{$rid} = $1;
    }

    for ($i=0; $i<$no_taxid; $i++) {
      $ll=<TMP>; chop($ll);
      my @lls = split(/\t/, $ll);
      push(@{ $ref_2_taxids{$rid} }, $lls[1]);
    }
  }
}
close(TMP);


my %scaffold_2_taxid = ();
my %taxid_member_scaffolds = ();
my %taxid_orf_count = ();
my %unbinned_scaffold = ();
my $scaffold_2_len = ();
my %sid_evidence = ();
#### round 0, take what is assigned from assembly-bin
open(TMP, $assembly_bin) || die "can not open $assembly_bin";
while($ll=<TMP>){
  chop($ll);
  next if ($ll =~ /^#/);
  my ($sid, $spid, $sp, $tid, $tname, @lls) = split(/\t/, $ll);

  next if ($tid eq "Host");
  next if ($tid eq "contaminant");
  $scaffold_2_len{$sid} = $lls[-1];
  if (not defined($scaffold_member_orfs{$sid})) {
    $scaffold_member_orfs{$sid} = []; 
    $scaffold_orf_count{$sid} = 0;
  }
  #next unless ($scaffold_member_orfs{$sid});
  if ($tid =~ /\d+/) {
    $scaffold_2_taxid{$sid} = $tid;
    $sid_evidence{$sid} = "mapping";
    if (not defined( $taxid_member_scaffolds{$tid} )) {
      $taxid_member_scaffolds{$tid} = [];
      $taxid_orf_count{$tid} = 0;
    }
    push(@{ $taxid_member_scaffolds{$tid} }, $sid);
    $taxid_orf_count{$tid} += $scaffold_orf_count{$sid} if (defined($scaffold_orf_count{$sid}));
  }
  else {
    $unbinned_scaffold{$sid} = 1;
  }
}
close(TMP);


open(LOG, "> $output_log") || die "can not write to $output_log";
#### assign taxid to scaffold
foreach $round (qw/1 2/) {
  #### first round assign scaffolds that can be uniquely assigned to taxid
  #### 2nd round if multiple taxids match a scaffold with same score, the taxid
  #### got more orfs get priority
  print LOG "####\tround $round to assign scaffolds\n";
  foreach $sid (@all_sids) {
    next if (defined $scaffold_2_taxid{$sid}); #### if sid got assigned in the first round

    my @orf_ids = @{$scaffold_member_orfs{$sid}};
    my $num_orfs = $#orf_ids+1;
    print LOG ">$sid\tORFs:$num_orfs\n";
  
    my %taxid_score = ();
    foreach $orf_id (@orf_ids) {
      next unless defined( $orf_2_hit{$orf_id} );
  
      my ($rid, $iden, $alnln, $frac) = @{ $orf_2_hit{$orf_id} };
      my $score = $iden * $alnln;
      print LOG "\tORF:$orf_id\t$rid\t$iden%\t$alnln\n";
  
      next unless (defined($ref_2_taxids{$rid}));
      my @t_taxids = @{ $ref_2_taxids{$rid} }; my $no1 = $#t_taxids+1;
      if ($no1 > 0) { foreach $i (@t_taxids) { $taxid_score{$i} += $score / $no1; } }
    }
  
    next unless (keys %taxid_score);
    my @taxid_score = keys %taxid_score; @taxid_score = sort {$taxid_score{$b} <=> $taxid_score{$a}} @taxid_score;
    
    foreach $i (@taxid_score) { print LOG "\ttaxid\t$i\t$taxid_score{$i}\n"}

    my $tid;
    if ($#taxid_score == 0) { #### only one taxid
      $tid = $taxid_score[0];
    }
    elsif ( $taxid_score{ $taxid_score[0]} > $taxid_score{ $taxid_score[1]} ) { #### first taxid score > 2nd
      $tid = $taxid_score[0];
    }
    elsif ($round == 2) {
      foreach $ii (@taxid_score) { $taxid_orf_count{$ii} = 0 unless (defined($taxid_orf_count{$ii})); }
      @taxid_score = sort { (    $taxid_score{$b} <=>     $taxid_score{$a}) or 
                            ($taxid_orf_count{$b} <=> $taxid_orf_count{$a}) } @taxid_score;
      $tid = $taxid_score[0];
    }
    else { #### next if this is first round and no uniq top taxid
      next;
    }

    $scaffold_2_taxid{$sid} = $tid;
    $sid_evidence{$sid} = "kegg";
    if (not defined( $taxid_member_scaffolds{$tid} )) {
      $taxid_member_scaffolds{$tid} = [];
      $taxid_orf_count{$tid} = 0;
    }
    push(@{ $taxid_member_scaffolds{$tid} }, $sid);
    delete $unbinned_scaffold{$sid};
    $taxid_orf_count{$tid} += $scaffold_orf_count{$sid};
  }
}

close(LOG);


######################### output annotation table
my @ranks = qw/Superkingdom Kingdom Phylum Class Order Family Genus Species Genome/;
open(SCA, "> $output_sca") || die "can not write to $output_sca";
open(OUT, "> $output_ann") || die "can not write to $output_ann";
open(TAX, "> $output_tax") || die "can not write to $output_tax";
print TAX "#Species_taxid\tTax_id\t", join("\t", @ranks), "\tNumber_scaffolds\tNumber_ORFs\tSum_depth_x_length\n";
print SCA "#Species_taxid\tTax_id\t", join("\t", @ranks), "\tScaffold\tLength\tNumber_ORFs\tDepth\tEvidence\n";
print OUT "#Species_taxid\tTax_id\t", join("\t", @ranks), "\tScaffold\tORF\tStart\tEnd\tFrame\tIden%\tFrac_alignment\tFamily\tDescription\tDepth\tEvidence\n";

#### output annotation with taxid
my @all_tids = keys %taxid_member_scaffolds;
   @all_tids = sort { $taxid_orf_count{$b} <=> $taxid_orf_count{$a} } @all_tids;

my %sptid_member_tids = ();
my %sptid_orf_count = ();
foreach $tid (@all_tids) {
  my @tid_info = @{$taxon_info{$tid}};
  my $sptid = $tid_info[15];
  $sptid = "None" unless ($sptid);
  if (not defined($sptid_member_tids{$sptid})) {
    $sptid_member_tids{$sptid} = [];
    $sptid_orf_count{$sptid} = 0;
  }
  push(@{$sptid_member_tids{$sptid}}, $tid);
  $sptid_orf_count{$sptid} += $taxid_orf_count{$tid};
}
my @all_sptids = keys %sptid_member_tids;
   @all_sptids = sort { $sptid_orf_count{$b} <=> $sptid_orf_count{$a} } @all_sptids;

my $with_contaminant = 0;
foreach $sptid (@all_sptids) {
  foreach $tid (@{ $sptid_member_tids{$sptid} }) {
    if ($contaminant_file) {
      if ($contaminant_tids{ $tid }) {
        $with_contaminant = 1; 
        next;
      }
    }
    my @sids = @{ $taxid_member_scaffolds{$tid} };
       @sids = sort { $scaffold_orf_count{$b} <=> $scaffold_orf_count{$a} } @sids;
    my @tid_info = @{$taxon_info{$tid}};
    my $tid_str = "$tid_info[16]\t$tid\t$tid_info[1]\t$tid_info[3]\t$tid_info[5]\t$tid_info[7]\t$tid_info[9]\t$tid_info[11]\t$tid_info[13]\t$tid_info[15]\t$tid_info[0]";
    print TAX "$tid_str\t", $#sids+1, "\t$taxid_orf_count{$tid}";
    my $sum_depth = 0;
    foreach $sid (@sids) {
      my @orf_ids = @{$scaffold_member_orfs{$sid}};
      my $num_orfs = $#orf_ids+1;
      print SCA "$tid_str\t$sid\t$scaffold_2_len{$sid}\t$num_orfs\t$sid_2_depth{$sid}\t$sid_evidence{$sid}\n";
      $sum_depth += $scaffold_2_len{$sid} * $sid_2_depth{$sid};
      next unless ($num_orfs>0);

      foreach $orf_id (@orf_ids) {
        my $ann = "hypothetical protein";
        my $KO  = "";
        my $iden1 = "-";
        my $frac1 = 1;
        if ( defined( $orf_2_hit{$orf_id} ) ) {
          my ($rid, $iden, $alnln, $frac) = @{ $orf_2_hit{$orf_id} };
          $ann = $ref_2_ann{$rid} if (defined($ref_2_ann{$rid}));
          $iden1 = "$iden%";
          $frac1 = $frac;
          $KO = $ref_2_KO{$rid} if (defined($ref_2_KO{$rid}));
        }
        print OUT "$tid_str\t$sid\t$orf_id\t$orf_info{$orf_id}\t$iden1\t$frac1\t$KO\t$ann\t$sid_2_depth{$sid}\t$sid_evidence{$sid}\n";
      }
    }
    print TAX "\t$sum_depth\n";
  }
}

#### output scaffolds without taxid
my $no_unknown_sid = 0;
my $no_unknown_orf = 0;
my @unbinned = keys %unbinned_scaffold;
   @unbinned = sort { $scaffold_2_len{$b} <=> $scaffold_2_len{$a} } @unbinned;
my $sum_depth = 0;

#foreach $sid (@all_sids) {
my $tid_str = "Unknown" . ("\tUnknown"x10);
foreach $sid (@unbinned) {
  next if defined($scaffold_2_taxid{$sid});
  my $tid = "Unknown";
  my $num_orfs = 0;
  my @orf_ids = ();
  if (defined( $scaffold_member_orfs{$sid} )) {
    @orf_ids = @{$scaffold_member_orfs{$sid}};
    $num_orfs = $#orf_ids+1;
  }
  print SCA "$tid_str\t$sid\t$scaffold_2_len{$sid}\t$num_orfs\t$sid_2_depth{$sid}\tNone\n";
  $sum_depth += $scaffold_2_len{$sid} * $sid_2_depth{$sid};
  next unless ($num_orfs>0);

  foreach $orf_id (@orf_ids) {
    my $ann = "hypothetical protein";
    my $KO  = "";
    my $iden1 = "-";
    my $frac1 = "-";
    if ( defined( $orf_2_hit{$orf_id} ) ) {
      my ($rid, $iden, $alnln, $frac) = @{ $orf_2_hit{$orf_id} };
      $ann = $ref_2_ann{$rid}; 
      $iden1 = "$iden%";
      $frac1 = $frac;
      $KO = $ref_2_KO{$rid} if (defined($ref_2_KO{$rid}));
    }
    print OUT "$tid_str\t$sid\t$orf_id\t$orf_info{$orf_id}\t$iden1\t$frac1\t$KO\t$ann\t$sid_2_depth{$sid}\tNone\n";
  }
  $no_unknown_sid++;
  $no_unknown_orf += $scaffold_orf_count{$sid};
}
print TAX "$tid_str\t$no_unknown_sid\t$no_unknown_orf\t$sum_depth\n";
close(OUT);
close(TAX);
close(SCA);

if ($with_contaminant) {
  open(OUTC, "> $output_contaminant") || die "can not write to $output_contaminant";

  foreach $sptid (@all_sptids) {
    foreach $tid (@{ $sptid_member_tids{$sptid} }) {
      next unless ($contaminant_tids{ $tid });
      my @sids = @{ $taxid_member_scaffolds{$tid} };
         @sids = sort { $scaffold_orf_count{$b} <=> $scaffold_orf_count{$a} } @sids;
      my @tid_info = @{$taxon_info{$tid}};
      foreach $sid (@sids) {
        my @orf_ids = @{$scaffold_member_orfs{$sid}};
        my $num_orfs = $#orf_ids+1;
        print OUTC "$tid_info[16]\t$tid_info[15]\t$tid\t$tid_info[0]\t$sid\t$scaffold_2_len{$sid}\t$num_orfs\t$sid_2_depth{$sid}\t$sid_evidence{$sid}\n";

      }
    }
  }
  close(OUTC);
}


sub ORF_info {
  my $ll = shift;
  my ($start, $end, $frame) = ("-","-","-");
#metagene format
#>MSA-1000-10-SE|scaffold|248.1 /source=MSA-1000-10-SE|scaffold|248 /start=2 /end=667 /frame=2 /length=221
#>MSA-1000-10-SE|scaffold|248.8 /source=MSA-1000-10-SE|scaffold|248 /start=7784 /end=8401 /frame=-1 /length=205

  if ( $ll =~ /\/start=(\d+)\s+\/end=(\d+)\s+\/frame=(\S+)/ ) {
    $start = $1; $end = $2; $frame = $3;
  }
  elsif ( $ll =~ /\s+# (\d+) # (\d+) # (\S+) # ID/) {
    $start = $1; $end = $2; $frame = $3;
  }
#prodigal format
#>MSA-1000-10-SE|scaffold|248_1 # 2 # 667 # 1 # ID=1_1;partial=10;start_type=Edge;rbs_motif=None;rbs_spacer=None;gc_cont=0.677
#>MSA-1000-10-SE|scaffold|248_6 # 6308 # 6658 # -1 # ID=1_6;partial=00;start_type=ATG;rbs_motif=AGGAG;rbs_spacer=5-10bp;gc_cont=0.687

  return ($start, $end, $frame); 
}

sub usage {
<<EOD;
$script_name -i blast_alignment_file -r cluster_info -a input ORF -o output ORF file -t tax_file

  options:
    -i blast alignment file in tab format
       can also be a name of a directory, which has multiple blast alignment files 
    -r cluster information file
    -a input ORF fasta file, used in blast search
    -t taxon info file, created by ~/git/ngomicswf/NGS-tools/taxon_print_tid_rank_table.pl based on blast ref db
    -o output prefix , the script creates following files
       output-ann.txt
       output-tax.txt
       output-tax.txt
    -e expect_cutoff, default 1e-6
    -s pre-binned assembly file
    -x taxon info file, created by ~/git/ngomicswf/NGS-tools/taxon_print_tid_rank_table.pl based on blast ref db
       this is different from -t, -t specify taxon file for kegg reference, -x specify taxon file for ref-genome,
       which is used to bin assembly
    -d depth of coverage file for assembly
    -X taxids of contaminants, optional, if exist, will label these as Contaminant
EOD
}

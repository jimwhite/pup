script2notebook
 We've made some fixes to the ACL2 certified book execution code (notably in the kernel).  We need to convert all the source files in $ACL2_HOME (/home/acl2) to notebooks, using fenced markdown.  Then do all the execution including the bootstrapping for ACL2 itself.  We don't have enough docs to explain the correct process and I don't remember all the details.  This code has been thru several iterations so not everything you see in the code is correct/relevant anymore.  We're using the lisp2nb version of the conversion.  Please examine the docs and code and write up the correct steps I need to do. 
BOOT-METADATA.md
 

I found an important divergence: conversion is now driven by lisp2nb in the acl2-jupyter Makefile, while Python build-notebooks mainly handles execution/injection utilities. I’ll confirm build-boot-strap and inject-boot-metadata behavior next so I can give you an accurate, current sequence.

Use this as the current, correct runbook for your setup (with lisp2nb, fenced markdown, and ACL2 bootstrap execution).

What’s current vs stale
The active workflow is defined in external/acl2-jupyter/Makefile.
Conversion is done with lisp2nb (sbcl --load .../lisp2nb.lisp) and uses :markdown-bracket :fenced.
Python build-notebooks is mainly used for execution helpers, not primary conversion.
For ACL2 source bootstrap execution, the newer path is build-boot-strap / make bootstrap-pass2.
The older boot-metadata + inject-boot-metadata flow still exists, but is more of a legacy capture/injection path.
End-to-end steps (recommended)
Run everything from:

cd /workspaces/pup-cursor/external/acl2-jupyter
1) Prepare tooling
make install-script2notebook
This creates/uses .venv and installs the Python CLI tools (build-notebooks, build-boot-strap, etc.).

2) Convert all ACL2 sources in /home/acl2 to notebooks (fenced markdown)
make lisp2nb-books NOTEBOOK_JOBS=`nproc`
This recursively converts all .lisp/.lsp (excluding .sys) to .ipynb in place, using lisp2nb with fenced markdown.

3) Execute certified books (normal notebook execution phase)
make notebooks-execute NOTEBOOK_JOBS=`nproc` NOTEBOOK_CELL_TIMEOUT=1800 NOTEBOOK_STARTUP_TIMEOUT=600
This executes notebooks for sources that have .cert, with portcullis handling (.port → injected acl2::eval-port-file cell).

4) Execute ACL2 bootstrap sources (ACL2 itself)
Preferred:

make bootstrap-pass2 NOTEBOOK_CELL_TIMEOUT=900 BOOTSTRAP_STARTUP_TIMEOUT=1200
Or full helper pipeline:

make bootstrap
bootstrap = deploy kernel code to Quicklisp local-projects + top-level conversion + pass2 bootstrap execution.

Practical “do everything” sequence
If you want one practical sequence:

make install-script2notebook
make -j `nproc` notebooks-convert
make notebooks-execute
make bootstrap-pass2 

Quick checks
Count notebooks:
find /home/acl2 -name '*.ipynb' -not -path '*/.sys/*' | wc -l
Spot-check a top-level bootstrap file:
ls /home/acl2/axioms.ipynb /home/acl2/boot-strap-pass-2-a.ipynb
Re-run safely/incrementally:
conversion/execution targets are incremental unless you use force targets/options.
If you want, I can also give you a minimal “fresh machine” checklist (including kernel install sanity checks) tailored to your exact container state.

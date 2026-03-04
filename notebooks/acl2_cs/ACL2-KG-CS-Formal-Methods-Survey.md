# ACL2 Knowledge Graph: CS Formal Methods Course Content Survey

> Comprehensive survey of CS-relevant content in the ACL2 community books,
> suitable for a university-level course in formal methods.
> Generated via ACL2 KG MCP semantic search against the ACL2Summary collection.

---

## 1. Sorting Algorithms

**Primary Library: `books/sorting/`**

| Book Path | Description |
|-----------|-------------|
| `books/sorting/` (directory) | Complete library of formally verified sorting algorithms: insertion sort, merge sort, quicksort, bubble sort. Includes ordering predicates (`ORDEREDP`, `TERM-ORDEREDP`), permutation reasoning, and equivalence theorems proving all algorithms produce identical results. Load via `(include-book "sorting/top")`. |
| `books/sorting/Readme.lsp` | Three approaches (equisort, equisort2, equisort3) to formally prove sorting algorithm correctness and equivalence. |
| `books/sorting/sorts-equivalent2.lisp` | Proves msort, qsort, and bsort all produce the same result as canonical isort. |
| `books/textbook/chap10/insertion-sort.lisp` | Textbook insertion sort with `insertion-sort-is-perm` theorem. |
| `books/textbook/chap11/qsort.lisp` | Quicksort with `less`/`notless` partitioning and recursive sorting. |
| `books/textbook/chap11/mergesort.lisp` | Merge sort: `split-list`, `merge2`, `mergesort` with correctness proofs. |
| `books/textbook/chap11/how-many-soln2.lisp` | Permutation theorem for mergesort: `(perm (mergesort x) x)`. |
| `books/powerlists/sort.lisp` | General theorems about number-lists useful for all sorting algorithms. |
| `books/powerlists/merge-sort.lisp` | Powerlist-based merge sort with permutation proof. |
| `books/powerlists/batcher-sort.lisp` | Batcher's bitonic/odd-even merge sort on powerlists. |
| `books/defsort/generic.lisp` | Generic sorting with abstract comparators; handles equivalence classes. |
| `books/defsort/defsort.lisp` | Macro generating verified mergesort with optional equivalence-to-insertion-sort proof. |
| `books/projects/groups/support/abelian.lisp` | Selection sort producing ordering permutations (via `cleq-ordering-perm-aux`). |
| `books/projects/taspi/code/tree-manip/quicksort.lisp` | Quicksort for phylogenetic tree manipulation. |
| `books/std/osets/sort.lisp` | `mergesort` for ordered sets with MBE dispatch. |

**Course relevance:** Excellent for teaching algorithm correctness proofs, permutation reasoning, and equivalence between algorithms.

---

## 2. Graph Algorithms (BFS, DFS, Shortest Path)

**Primary Libraries: `books/workshops/1999/graph/`, `books/misc/`**

| Book Path | Description |
|-----------|-------------|
| `books/workshops/1999/graph/` (directory) | Verified depth-first and linear path-finding for directed graphs. Core functions: `find-path`, `linear-find-path`. |
| `books/workshops/1999/graph/linear-find-path.lisp` | Linear-time DFS for simple paths with `linear-find-path` and `linear-find-next-step`. |
| `books/workshops/1999/graph/find-path1.lisp` | Verified DFS: `FIND-PATH` and `FIND-NEXT-STEP` with graph predicates. |
| `books/workshops/1999/graph/find-path2.lisp` | Lightweight graph model (association list), DFS with `pathp`, `simple-pathp`. |
| `books/misc/dijkstra-shortest-path.lisp` | **Dijkstra's shortest-path** for directed weighted graphs, fully verified. |
| `books/acl2s/demos/dsp-defuns.lisp` | `dijkstra-shortest-path` computing shortest paths using the DSP algorithm. |
| `books/acl2s/demos/dsp.lsp` | Verified Dijkstra-style shortest-path with predecessor tables and frontier sets. |
| `books/defexec/find-path/graph/linear-find-path.lisp` | BFS-style graph search using defexec. |
| `books/acl2s/cgen/simple-graph-array.lisp` | DFS on graph with vertex marking and finishing-order lists. |
| `books/workshops/2020/coglio-westfold/drone-plan/graph.lisp` | Shortest paths with node exclusion sets (drone path planning). |
| `books/workshops/2020/sumners/cycle-check.lisp` | `graph-enumerate`: DFS-based graph node enumeration. |
| `books/workshops/2013/hardin-hardin/support/APSP.lisp` | **All-Pairs Shortest Path (APSP)** for weighted graphs. |
| `books/projects/taspi/tree-generation/branch-and-bound/bandb.lisp` | Branch-and-bound with BFS (`bandb`) and DFS (`depth-bandb`). |

**Course relevance:** Strong coverage of classic graph algorithms with formal proofs of correctness. Dijkstra's and APSP are directly usable in algorithms courses.

---

## 3. Data Structures (Trees, BST, Hash Tables)

**Primary Library: `books/kestrel/data/treeset/`**

| Book Path | Description |
|-----------|-------------|
| `books/kestrel/data/treeset/` | Complete **treap** library (BST + heap), with insert, delete, split, min/max, membership, iteration. |
| `books/kestrel/data/treeset/internal/min-max.lisp` | BST property definitions and min/max operations. |
| `books/kestrel/data/treeset/internal/split.lisp` | `TREE-SPLIT` with BST correctness theorem. |
| `books/kestrel/data/treeset/internal/insert.lisp` | `TREE-INSERT` preserves BST invariant (proved). |
| `books/kestrel/data/treeset/internal/in.lisp` | Core membership predicates: `TREE-IN`, `tree-search-in`. |
| `books/kestrel/data/treeset/internal/in-order.lisp` | In-order traversal of BSTs. |
| `books/kestrel/data/treeset/internal/iter.lisp` | Iterator proofs: all trees in list are BSTs and heaps. |
| `books/kestrel/data/treeset/set.lisp` | Set operations via BST functions. |
| `books/kestrel/data/treeset/doc.lisp` | Documentation: treaps = BST + heap constraints. |
| `books/std/osets/` | Ordered sets library (finite sets as ordered lists). |
| `books/std/obags/core.lisp` | Ordered bags (multisets) as non-strictly ordered lists. |
| `books/workshops/2004/smith-et-al/support/bags/` | Full multiset/bag library with `BAG-INSERT`, `BAG-SUM`, `subbagp`, `unique`, `disjoint`. |
| `books/kestrel/data/hash/jenkins.lisp` | Jenkins hash function with termination proof. |

**Course relevance:** BST invariant proofs, treap verification, and ordered set/bag abstractions map directly to data structures curriculum.

---

## 4. Compilers & Programming Language Semantics

**Primary Libraries: `books/models/jvm/`, `books/workshops/1999/compiler/`**

| Book Path | Description |
|-----------|-------------|
| `books/workshops/1999/compiler/compiler.lisp` | Self-hosting compiler: `compiler-source`, `compiler-target`, compiles itself. |
| `books/workshops/1999/compiler/exercises.lisp` | Compiler bootstrapping exercises. |
| `books/models/jvm/m1-original/m1-story.lisp` | Compiler for arithmetic + assignment + while statements targeting M1 machine. |
| `books/projects/acl2-in-hol/tests/inputs/m1-story.lisp` | Same compiler development embedded in HOL. |
| `books/workshops/2000/lusk-mccune/lusk-mccune-final/compile.lisp` | Compiler for small imperative language with label handling. |
| `books/models/y86-old/y86-basic/py86/py86-code.lsp` | C → x86 → Y86 compiler translation (Fibonacci). |
| `books/projects/aleo/leo/early-version/tests/compiler-tests.lisp` | Leo language compiler tests. |
| `books/system/doc/acl2-doc.lisp` (bib entries) | References to verified compilers: Flatau (Nqthm→Piton), Gypsy→assembly, compiler correctness via operational semantics. |

**Course relevance:** Verified compiler construction from source to machine code, with correctness proofs relating source semantics to target execution.

---

## 5. Cryptography & Security

**Primary Libraries: `books/kestrel/crypto/`, `books/projects/security/`**

| Book Path | Description |
|-----------|-------------|
| `books/projects/security/jfkr/` | Verified **JFKr key exchange protocol**: symmetric/asymmetric encryption, keyed hashes, protocol steps, attacker models. |
| `books/projects/security/jfkr/encryption.lisp` | `encrypt-symmetric-list`, `encrypt-asymmetric-list`, `compute-keyed-hash` with collision-resistance proofs. |
| `books/projects/security/jfkr/jfkr.lisp` | Protocol steps (`responder-step1`), signature verification, attacker resilience theorems. |
| `books/kestrel/crypto/chacha/chacha20-tests.lisp` | **ChaCha20** encryption test vectors. |
| `books/kestrel/crypto/ecurve/short-weierstrass.lisp` | Elliptic curve cryptography (short Weierstrass curves over prime fields). |
| `books/kestrel/crypto/r1cs/` | **R1CS** (Rank-1 Constraint Systems) for zero-knowledge proofs. |
| `books/kestrel/prime-fields/` | Prime field arithmetic library (foundation for crypto). |
| `books/projects/numbers/pratt.lisp` | **Pratt primality certificates** with `fast-mod-expt`, `order`, `find-order`. |
| `books/projects/numbers/support/pratt.lisp` | Proof that 2²⁵⁵-19 is prime (`primep-25519`). |
| `books/kestrel/ethereum/semaphore/` | Ethereum Semaphore circuit proofs. |
| `books/kestrel/zcash/gadgets/` | Zcash zero-knowledge proof gadgets. |

**Course relevance:** Protocol verification, encryption primitives, elliptic curves, and zero-knowledge proof systems.

---

## 6. Model Checking & Temporal Logic

**Primary Libraries: `books/workshops/1999/mu-calculus/`, `books/projects/sat/`**

| Book Path | Description |
|-----------|-------------|
| `books/workshops/1999/mu-calculus/` (directory) | μ-calculus model checker: set operations, monotonic fixpoints, finite-model construction, labeling. |
| `books/workshops/1999/mu-calculus/solutions/models.lisp` | 7-element model structure with states, relations, atomic propositions, labeling, and inverse relations. |
| `books/projects/sat/zz-resolution-checker/zzv-interface.lisp` | `zzv-modelcheck-when-proved`: verified model checking with property validation. |
| `books/centaur/aig/bddify.lisp` | **BDD**-based satisfiability without external SAT solver. |
| `books/bdd.lisp` | Core BDD operations in ACL2. |

**Course relevance:** μ-calculus model checking, BDD-based verification, and SAT-based property checking.

---

## 7. Concurrency, Parallelism & Distributed Systems

**Primary Libraries: `books/projects/aleo/bft/`, `books/workshops/2002/`**

| Book Path | Description |
|-----------|-------------|
| `books/projects/aleo/bft/` | **AleoBFT consensus protocol** (extended Bullshark): dynamic committees, stake, fault tolerance (no-fork safety proofs). |
| `books/projects/aleo/bft/fault-tolerance.lisp` | `system-committees-fault-tolerant-p`: BFT fault tolerance predicates and backward preservation theorems. |
| `books/projects/aleo/bft/messages.lisp` | Network message model with authentication assumptions. |
| `books/projects/aleo/bft/top.lisp` | Top-level BFT correctness: safety and liveness under partially synchronous model. |
| `books/workshops/2002/georgelin-borrione-ostier/support/` | `make-rec-concurrent-stat`: concurrent state machine generation. |
| `books/workshops/2003/sumners/support/fair1.lisp` | Fairness predicates for concurrent system modeling. |
| `books/quicklisp/bundle/software/bordeaux-threads-v0.9.3/` | Concurrent programming primitives with shared variable tests. |

**Course relevance:** Byzantine fault tolerance proofs, consensus protocol verification, fairness in concurrent systems.

---

## 8. Automata, Regular Expressions & Finite State Machines

**Primary Libraries: `books/workshops/2025/medley-manolios/`, `books/projects/async/`**

| Book Path | Description |
|-----------|-------------|
| `books/workshops/2025/medley-manolios/top.lisp` | Complete **automata** library: DFA, NFA definitions and theorems. |
| `books/workshops/2025/walter-manolios/examples/regular-expressions.lisp` | Regular expression reasoning with Z3-backed solver. |
| `books/projects/async/serial-adder/32-bit-serial-adder-old/de.lisp` | DE language for **FSM** representation with primitives modeled as state machines. |
| `books/workshops/2009/pierre-clavel-leveugle/Fault-tolerance/register-det.lisp` | FSM `REG-det` for hardware register with error detection. |
| `books/centaur/sv/svtv/fsm-base.lisp` | `fsm-eval-states`: FSM evaluation with state mapping guards. |
| `books/centaur/sv/svtv/fsm.lisp` | Symbolic FSM execution (`fsm-run-outs-and-states-symbolic`). |
| (directory) | Cellular automata (1D, 8-bit) and general FSM library with types, constructors, evaluation. |

**Course relevance:** Formal automata theory with DFA/NFA, regular expressions, and FSM modeling for both software and hardware.

---

## 9. Complexity Theory & Algorithm Analysis

| Book Path | Description |
|-----------|-------------|
| `books/projects/milawa/ACL2/logic/arities-okp.lisp` | Discusses O(n log n) vs O(n²) complexity comparison for sorting vs `subsetp`. |
| `books/workshops/2004/davis/support/primitives.lisp` | `setp` has linear time complexity. |
| `books/workshops/2004/davis/support/sort.lisp` | Union's linear complexity facilitates reasoning. |
| `books/textbook/chap11/tautology.lisp` | `if-complexity`: multiplicative expression measure for tautology checking. |
| `books/demos/marktoberdorf-08/preliminary-material.lisp` | `UNIVERSAL-ALGORITHM`: universal computation primitive. |
| `books/projects/codewalker/terminatricks.lisp` | Algorithm finding minimal hypothesis subsets under resource constraints. |
| `books/defexec/dag-unification/dag-unification-l.lisp` | Most general unifier algorithm with correctness proofs. |

**Course relevance:** Useful as supplementary material for algorithm analysis; complexity measures and termination arguments.

---

## 10. Arithmetic & Number Theory

**Primary Libraries: `books/arithmetic-*/`, `books/kestrel/arithmetic-light/`, `books/projects/numbers/`**

| Book Path | Description |
|-----------|-------------|
| `books/arithmetic-3/` | Comprehensive arithmetic library with bind-free and meta rules. |
| `books/arithmetic-5/` | Extended arithmetic with normalization and distribution. |
| `books/kestrel/arithmetic-light/mod-expt-fast.lisp` | Fast modular exponentiation. |
| `books/projects/numbers/pratt.lisp` | **Pratt primality testing**: `fast-mod-expt`, `order`, `find-order`, `max-order-p`. |
| `books/projects/numbers/support/pratt.lisp` | Primality certificate for 2²⁵⁵-19. |
| `books/projects/numbers/support/fermat.lisp` | Pigeonhole induction for Fermat's theorem. |
| `books/projects/numbers/support/binomial.lisp` | Binomial expansion with termination proofs. |
| `books/kestrel/prime-fields/fep.lisp` | Prime field element predicates. |
| `books/kestrel/number-theory/primes` | Prime number theory library. |
| `books/rtl/` | **RTL library** for IEEE-754 floating-point arithmetic: encoding, rounding, error bounds. |
| `books/rtl/rel4/support/ereps.lisp` | Floating-point bit-vector encoding/decoding (sign, exponent, significand). |
| `books/projects/arm/second/fdiv8/fdiv8.lisp` | ARM floating-point division verification. |

**Course relevance:** Number theory foundations (primality, modular arithmetic), IEEE-754 floating-point verification.

---

## 11. Hardware Verification & Processor Models

**Primary Libraries: `books/projects/fm9001/`, `books/projects/x86isa/`, `books/centaur/`**

| Book Path | Description |
|-----------|-------------|
| **FM9001 Processor** | |
| `books/projects/fm9001/` (directory) | Complete FM-9801 processor: abstract ISA, micro-architecture, correctness invariants. |
| `books/projects/fm9001/hard-spec.lisp` | Hardware specification with 4-valued gate-level and Boolean equivalence. |
| `books/projects/fm9001/dual-port-ram.lisp` | Dual-port RAM model (sequential, no clock). |
| **X86 ISA Model** | |
| `books/projects/x86isa/` (directory) | Full x86 ISA model: 400+ opcodes, segmentation, paging, instruction decoding. |
| `books/projects/x86isa/machine/state.lisp` | X86 state components. |
| `books/projects/x86isa/linux/` | Linux kernel booting in x86isa model (bzImage loading, page tables). |
| `books/kestrel/x86/` | Kestrel x86 library: state type, memory, flags, registers, run-until-return. |
| **Y86 Teaching Architecture** | |
| `books/models/y86-old/` | Y86 toy x86 model for teaching computer architecture. |
| `books/models/y86-old/y86-basic/py86/py86-state.lisp` | `x86-32p`: 5-element processor state predicate. |
| **Centaur Hardware Verification** | |
| `books/centaur/sv/` (directory) | **Symbolic Vector (SV)** environment for formal hardware verification. |
| `books/centaur/esim/` | ESIM hardware verification tutorial and framework. |
| `books/centaur/aignet/` | AIG network: gate reduction, sweeping, equivalence checking. |
| `books/centaur/vl2014/` | Verilog parser and transformation pipeline. |
| **RTL Verification** | |
| (directory) | IEEE-754 64-bit FP multiplier: RTL model, compiler to ACL2, symbolic model with correctness proof. |
| `books/projects/async/` | Asynchronous circuit verification: serial adders, link joints, arbitration. |

**Course relevance:** Flagship content for hardware verification courses. FM9001 and x86isa are landmark verified processors. SV/ESIM provide industrial-strength verification.

---

## 12. Operating Systems & Kernels

| Book Path | Description |
|-----------|-------------|
| `books/projects/x86isa/linux/` (directory) | Library for **booting Linux** in the x86isa model: bzImage loading, page tables, peripherals, TTY. |
| `books/projects/x86isa/linux/doc.lisp` | `linux-load`: loads Linux bzImage + rootfs into x86isa model. Describes kernel init, root mounting, `pivot_root`. |
| `books/system/doc/acl2-doc.lisp` (bib::bevier87) | Bevier's 1987 **verified OS kernel** dissertation. |
| `books/system/doc/acl2-doc.lisp` (bib::bhmy89) | First verified OS kernel for a practical machine. |
| `books/projects/milawa/doc.lisp` | **Milawa**: verified theorem prover with kernel correctness down to x86 machine code (via Jitawa/HOL4). |

**Course relevance:** OS kernel verification history, running real Linux in a formal model.

---

## 13. Network Protocol Verification

**Primary Libraries: `books/projects/aleo/bft/`, `books/projects/security/jfkr/`, `books/demos/modeling/`**

| Book Path | Description |
|-----------|-------------|
| (directory: XY-routing) | Verified **XY-routing** for 2D mesh networks. |
| (directory: hexnet) | Verified **hexagonal packet-routing network** with graph, packet, link, and arbiter models. |
| `books/projects/aleo/bft/messages.lisp` | BFT message model with sender authentication. |
| `books/projects/aleo/bft/system-states.lisp` | `SYSTEM-STATEP`: protocol state with validators and network fields. |
| `books/projects/security/jfkr/jfkr.lisp` | JFKr protocol steps, attacker resilience (`RUN-5-STEPS-WITH-POORLY-FORMED-ATTACKER-YIELDS-INITIATOR-FAILURE`). |
| `books/demos/modeling/network-state-basic.lisp` | `valid-network`: simple network state validation. |
| `books/demos/modeling/network-state.lisp` | Network packet retrieval with correctness theorem. |
| `books/quicklisp/bundle/software/cl+ssl-20231021-git/` | CL+SSL: verified OpenSSL interface with BIO, SSL contexts, certificates. |

**Course relevance:** Protocol verification methodology, network modeling fundamentals, security protocol analysis.

---

## 14. Lambda Calculus & Type Theory

| Book Path | Description |
|-----------|-------------|
| `books/workshops/2006/swords-cook/lcsoundness/LambdaCalcSoundness.lisp` | **Lambda calculus soundness**: `VALID-TYPING` proofs for typed lambda expressions (e.g., `FUN(BOOL)(BOOL)`). |
| `books/projects/milawa/ACL2/build/equal.lisp` | Lambda calculus support library. |
| `books/projects/milawa/ACL2/bootstrap/logic/terms-3.lisp` | Lambda term classification. |
| `books/workshops/2020/peng-greenstreet/typed-term.lisp` | Typed terms with lambda kind tags and pseudo-lambda structure proofs. |
| `books/clause-processors/pseudo-term-fty.lisp` | `pseudo-term-lambda` with FTY integration. |
| `books/kestrel/apt/propagate-iso.lisp` | `ISO-TYPE-THEOREM`: recursive type theorem for lambda abstractions. |
| `books/projects/smtlink/verified/pseudo-lambda-lemmas.lisp` | Pseudo-lambda predicate theory. |
| `books/projects/pltpa/pltpa.lisp` | Type function construction with BOOLEAN/NUMERIC properties. |

**Course relevance:** Typed lambda calculus with soundness proofs; type-theoretic reasoning in a theorem prover.

---

## 15. Induction, Recursion & Termination

| Book Path | Description |
|-----------|-------------|
| `books/projects/numbers/support/fermat.lisp` | `PIGEONHOLE-INDUCTION` with measure `len l`. |
| `books/system/doc/tours.lsp` | Induction scheme legality tied to APP recursion termination proof. |
| `books/workshops/2007/cowles-et-al/support/greve/defminterm.lisp` | `vfaat_fnx_induction_terminates_property`: characterizing induction termination. |
| `books/coi/defpun/defminterm.lisp` | Properties of induction termination via `vfaat_fn`. |
| `books/projects/codewalker/terminatricks.lisp` | Termination tricks: minimal hypothesis subsets for termination proofs. |
| `defuns.lisp` (ACL2 source) | `prove-termination-recursive`: core termination prover for recursive cliques. |
| Multiple books | Standard pattern: `(declare (xargs :measure ...))` with `nfix`, `acl2-count`, etc. |

**Course relevance:** Foundational for any formal methods course — induction principles, well-founded recursion, termination arguments.

---

## 16. SAT Solvers & Boolean Reasoning (Bonus Topic)

**Primary Libraries: `books/clause-processors/SULFA/`, `books/projects/sat/`, `books/centaur/aig/`**

| Book Path | Description |
|-----------|-------------|
| `books/clause-processors/SULFA/books/sat-tests/` | Incremental SAT solver tests. |
| `books/projects/sat/proof-checker-itp13/` | **SAT proof checker** with resolution-based verification. |
| `books/projects/sat/lrat/` | **LRAT proof checker**: verified DRAT/LRAT certificate checking. |
| `books/centaur/aig/bddify.lisp` | BDD-based satisfiability (no external solver needed). |
| `books/centaur/aignet/` | AIG network operations: construction, sweeping, gate reduction. |
| `books/projects/smtlink/` | **SMT integration**: connecting ACL2 to SMT solvers (Z3). |
| `books/workshops/2025/walter-manolios/examples/` | Z3-backed examples: sets, numbers, regular expressions, ACL2 demos. |

**Course relevance:** SAT/SMT solving, proof checking, BDD operations — core to automated reasoning curriculum.

---

## Summary: Recommended Course Modules

| Module | Primary ACL2 Books | Depth |
|--------|-------------------|-------|
| **Intro to Formal Verification** | `books/textbook/`, `books/system/doc/tours.lsp` | ★★★ |
| **Induction & Termination** | `defuns.lisp`, `books/coi/defpun/`, `books/workshops/2007/cowles-et-al/` | ★★★ |
| **Sorting Algorithm Correctness** | `books/sorting/`, `books/textbook/chap10-11/` | ★★★ |
| **Data Structures (BST, Sets, Bags)** | `books/kestrel/data/treeset/`, `books/std/osets/`, `books/std/obags/` | ★★★ |
| **Graph Algorithms** | `books/workshops/1999/graph/`, `books/misc/dijkstra-shortest-path.lisp` | ★★★ |
| **Compiler Verification** | `books/workshops/1999/compiler/`, `books/models/jvm/m1-original/` | ★★☆ |
| **Arithmetic & Number Theory** | `books/arithmetic-3/`, `books/projects/numbers/` | ★★★ |
| **Cryptography** | `books/kestrel/crypto/`, `books/projects/security/jfkr/` | ★★☆ |
| **Hardware/Processor Verification** | `books/projects/fm9001/`, `books/projects/x86isa/`, `books/centaur/` | ★★★ |
| **Automata & FSMs** | `books/workshops/2025/medley-manolios/`, `books/projects/async/` | ★★☆ |
| **SAT/SMT & Boolean Reasoning** | `books/projects/sat/`, `books/centaur/aig/`, `books/centaur/aignet/` | ★★★ |
| **Network & Protocol Verification** | `books/projects/aleo/bft/`, `books/projects/security/jfkr/` | ★★☆ |
| **Model Checking** | `books/workshops/1999/mu-calculus/` | ★★☆ |
| **Lambda Calculus & Types** | `books/workshops/2006/swords-cook/lcsoundness/` | ★★☆ |
| **OS Kernel Verification** | `books/projects/x86isa/linux/`, Bevier87 dissertation | ★☆☆ |
| **BFT/Consensus** | `books/projects/aleo/bft/` | ★★☆ |

> ★★★ = Rich, self-contained content ready for course use  
> ★★☆ = Solid content, may need supplementary material  
> ★☆☆ = Reference/historical content, useful as reading

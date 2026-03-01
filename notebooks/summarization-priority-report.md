# ACL2 Notebook Summarization Priority Report

**Goal**: Select ~500 notebooks from the ACL2 community books for LLM summarization,
organized into 5 batches of ~100. The summaries will be used by an ACL2 coding agent
to create teaching notebooks that explain ACL2 techniques, idioms, and common patterns.

**Existing summaries**: 52 notebook-level summaries exist for ACL2 system internals
(axioms.lisp, prove.lisp, rewrite.lisp, defthm.lisp, etc.). These are excluded below.

**Selection criteria**:
- How to use ACL2: methods, techniques, idioms
- Writing lemmas and theorems effectively
- Important libraries (FTY, std, etc.)
- Tools for investigating code and diagnosing proof failures
- Getting proofs to work: hints, debugging, proof strategies

---

## Batch 1: Foundation — FTY Type System & Core Data Types (~100 notebooks)

**Rationale**: FTY (Fixtype) is the most important type-definition framework in modern
ACL2. Every serious ACL2 project uses FTY to define product types, sum types, lists,
alists, and options. The std/basic library provides fundamental arithmetic equivalences
and type fixes. std/osets provides ordered sets. Ordinals are essential for termination
proofs. These must be summarized first as they underpin all other work.

### centaur/fty/ — ALL 37 notebooks

The FTY library defines the `defprod`, `deftagsum`, `deflist`, `defalist`, `defoption`,
`defflexsum`, and `deftypes` macros that generate type recognizers, constructors,
accessors, fixing functions, and equivalence relations. Key topics to extract:

| # | Notebook | Cells | Teaching Focus |
|---|---------|------:|----------------|
| 1 | books/centaur/fty/deftypes.lisp | 64 | **Core**: mutual recursion, type definition |
| 2 | books/centaur/fty/fty-parseutils.lisp | 64 | Parsing/processing type definitions |
| 3 | books/centaur/fty/fty-sum-casemacro.lisp | 40 | Pattern matching on sum types |
| 4 | books/centaur/fty/database.lisp | 37 | FTY type database/registry |
| 5 | books/centaur/fty/fixtype.lisp | 34 | Fixtype infrastructure |
| 6 | books/centaur/fty/fty-transsum.lisp | 32 | Transparent sum types |
| 7 | books/centaur/fty/fty-alist.lisp | 28 | Typed alist definitions |
| 8 | books/centaur/fty/fty-list.lisp | 27 | Typed list definitions |
| 9 | books/centaur/fty/fty-omap.lisp | 26 | Ordered map types |
| 10 | books/centaur/fty/fty-sugar.lisp | 23 | Syntactic sugar for FTY |
| 11 | books/centaur/fty/fty-set.lisp | 22 | Typed set definitions |
| 12 | books/centaur/fty/bitstruct.lisp | 21 | Bit-level struct definitions |
| 13 | books/centaur/fty/fixequiv.lisp | 20 | Fix/equiv theory |
| 14 | books/centaur/fty/fty-defvisitor-base.lisp | 20 | Visitor pattern base |
| 15 | books/centaur/fty/fty-defvisitor-multi.lisp | 19 | Multi-type visitors |
| 16 | books/centaur/fty/fty-defvisitor.lisp | 19 | Visitor pattern definition |
| 17 | books/centaur/fty/basetypes.lisp | 18 | Base type fixers (nat, int, bool, etc.) |
| 18 | books/centaur/fty/fty-table.lisp | 17 | FTY type lookup tables |
| 19 | books/centaur/fty/deftypes-tests.lisp | 16 | Usage examples and tests |
| 20 | books/centaur/fty/bitstruct-tests.lisp | 15 | Bitstruct usage examples |
| 21 | books/centaur/fty/fty-defvisitor-multi-tests.lisp | 12 | Visitor pattern examples |
| 22 | books/centaur/fty/fty-option.lisp | 12 | Option/Maybe type |
| 23 | books/centaur/fty/tests-utils.lisp | 12 | Test utilities |
| 24 | books/centaur/fty/baselists.lisp | 11 | Base list types |
| 25 | books/centaur/fty/tests.lisp | 11 | Test cases |
| 26 | books/centaur/fty/fty-count.lisp | 10 | Count measure for types |
| 27 | books/centaur/fty/fty-defvisitor-tests.lisp | 10 | Visitor examples |
| 28 | books/centaur/fty/top.lisp | 10 | Top-level includes |
| 29 | books/centaur/fty/portcullis.lisp | 9 | Package definitions |
| 30 | books/centaur/fty/acl2-customization.lsp | 8 | Customization |
| 31 | books/centaur/fty/fty-parseutils-tests.lisp | 8 | Parse utility tests |
| 32 | books/centaur/fty/multiconsp.lisp | 7 | Multi-cons predicate |
| 33 | books/centaur/fty/fty-defvisitor-base-tests.lisp | 6 | Visitor base tests |
| 34 | books/centaur/fty/fty-omap-tests.lisp | 6 | OMap tests |
| 35 | books/centaur/fty/fty-sum-casemacro-tests.lisp | 5 | Case macro tests |
| 36 | books/centaur/fty/fty-table-tests.lisp | 5 | Table tests |
| 37 | books/centaur/fty/package.lsp | 4 | Package definition |

### std/basic/ — ALL 36 notebooks

Fundamental type fixes, arithmetic equivalences, and basic predicates. Key:
`arith-equivs.lisp` (140 cells) defines int-equiv, nat-equiv, bit-equiv used everywhere.

| # | Notebook | Cells |
|---|---------|------:|
| 38 | books/std/basic/arith-equivs.lisp | 140 |
| 39 | books/std/basic/top.lisp | 30 |
| 40 | books/std/basic/defs.lisp | 23 |
| 41 | books/std/basic/arith-equiv-defs.lisp | 14 |
| 42 | books/std/basic/inductions.lisp | 11 |
| 43 | books/std/basic/two-nats-measure.lisp | 10 |
| 44 | books/std/basic/symbol-package-name-non-cl-tests.lisp | 10 |
| 45 | books/std/basic/organize-symbols-by-name-tests.lisp | 9 |
| 46 | books/std/basic/organize-symbols-by-name.lisp | 9 |
| 47 | books/std/basic/organize-symbols-by-pkg-tests.lisp | 9 |
| 48 | books/std/basic/organize-symbols-by-pkg.lisp | 9 |
| 49 | books/std/basic/symbol-package-name-lst-tests.lisp | 9 |
| 50 | books/std/basic/symbol-name-lst.lisp | 8 |
| 51 | books/std/basic/bytep.lisp | 7 |
| 52 | books/std/basic/good-pseudo-term-listp.lisp | 7 |
| 53 | books/std/basic/good-pseudo-termp.lisp | 7 |
| 54 | books/std/basic/good-valuep.lisp | 7 |
| 55 | books/std/basic/if-star.lisp | 7 |
| 56 | books/std/basic/maybe-string-fix.lisp | 7 |
| 57 | books/std/basic/nibblep.lisp | 7 |
| 58 | books/std/basic/symbol-package-name-non-cl.lisp | 7 |
| 59 | books/std/basic/symbol-package-name-lst.lisp | 7 |
| 60 | books/std/basic/code-char-char-code-with-force.lisp | 5 |
| 61 | books/std/basic/controlled-configuration.lisp | 6 |
| 62 | books/std/basic/fix.lisp | 6 |
| 63 | books/std/basic/ifix.lisp | 6 |
| 64 | books/std/basic/maybe-natp.lisp | 6 |
| 65 | books/std/basic/mbt-dollar.lisp | 6 |
| 66 | books/std/basic/member-symbol-name.lisp | 6 |
| 67 | books/std/basic/nfix.lisp | 6 |
| 68 | books/std/basic/nonkeyword-listp.lisp | 6 |
| 69 | books/std/basic/pos-fix.lisp | 6 |
| 70 | books/std/basic/realfix.lisp | 6 |
| 71 | books/std/basic/rfix.lisp | 6 |
| 72 | books/std/basic/acl2-customization.lsp | 5 |
| 73 | books/std/basic/intern-in-package-of-symbol.lisp | 5 |

### std/osets/ — ALL 20 notebooks

Ordered sets (osets) library — finite set operations with canonicalized representation.
Heavily used in verification projects. `top.lisp` (228 cells) is the comprehensive
reference; `quantify.lisp` (57 cells) shows set quantification patterns.

| # | Notebook | Cells |
|---|---------|------:|
| 74 | books/std/osets/top.lisp | 228 |
| 75 | books/std/osets/quantify.lisp | 57 |
| 76 | books/std/osets/instance.lisp | 50 |
| 77 | books/std/osets/outer.lisp | 32 |
| 78 | books/std/osets/primitives.lisp | 26 |
| 79 | books/std/osets/quantify-tests.lisp | 26 |
| 80 | books/std/osets/membership.lisp | 24 |
| 81 | books/std/osets/map.lisp | 24 |
| 82 | books/std/osets/map-tests.lisp | 24 |
| 83 | books/std/osets/under-set-equiv.lisp | 22 |
| 84 | books/std/osets/computed-hints.lisp | 19 |
| 85 | books/std/osets/intersect.lisp | 17 |
| 86 | books/std/osets/sort.lisp | 17 |
| 87 | books/std/osets/element-list.lisp | 16 |
| 88 | books/std/osets/difference.lisp | 13 |
| 89 | books/std/osets/union.lisp | 11 |
| 90 | books/std/osets/cardinality.lisp | 6 |
| 91 | books/std/osets/delete.lisp | 6 |
| 92 | books/std/osets/nonemptyp.lisp | 6 |
| 93 | books/std/osets/acl2-customization.lsp | 6 |

### ordinals/ — 7 key notebooks (termination proofs)

Ordinals are fundamental to ACL2's termination proof mechanism. Every recursive function
needs a measure that decreases in an ordinal ordering.

| # | Notebook | Cells | Teaching Focus |
|---|---------|------:|----------------|
| 94 | books/ordinals/ordinal-basic-thms.lisp | 103 | Core ordinal theorems |
| 95 | books/ordinals/ordinal-exponentiation.lisp | 103 | Ordinal exponentiation |
| 96 | books/ordinals/ordinal-addition.lisp | 67 | Ordinal addition |
| 97 | books/ordinals/ordinal-multiplication.lisp | 62 | Ordinal multiplication |
| 98 | books/ordinals/ordinal-isomorphism.lisp | 57 | Isomorphism proofs |
| 99 | books/ordinals/ordinal-total-order.lisp | 41 | Total ordering |
| 100 | books/ordinals/ordinal-definitions.lisp | 36 | Core definitions |

**Batch 1 Total: 100 notebooks**

---

## Batch 2: Teaching & Proof Methodology (~100 notebooks)

**Rationale**: This batch focuses on explicitly pedagogical material — textbook exercises,
proof style comparisons, tutorial lectures, and demonstrations. These are the notebooks
most directly useful for teaching an agent how to write and structure proofs.

### textbook/ — ALL 26 notebooks

From the ACL2 textbook "Computer-Aided Reasoning: An Approach" by Kaufmann, Manolios,
and Moore. Contains worked exercises from Chapters 3-11 covering:
- Function definitions and recursion
- Logic-mode vs program-mode
- Induction strategies
- Sorting algorithms (insertion sort, mergesort, quicksort)
- Tautology checking, compression, finite sets

| # | Notebook | Cells | Teaching Focus |
|---|---------|------:|----------------|
| 1 | books/textbook/chap6/selected-solutions.lisp | 86 | Induction, proof strategies |
| 2 | books/textbook/chap4/solutions-logic-mode.lisp | 83 | Logic-mode programming |
| 3 | books/textbook/chap4/solutions-program-mode.lisp | 75 | Program-mode programming |
| 4 | books/textbook/chap11/tautology.lisp | 61 | Tautology checker verification |
| 5 | books/textbook/chap11/how-many-soln2.lisp | 50 | Complex counting problem |
| 6 | books/textbook/chap11/summations.lisp | 44 | Summation proofs |
| 7 | books/textbook/chap11/compress.lisp | 43 | Data compression proof |
| 8 | books/textbook/chap11/qsort.lisp | 42 | Quicksort verification |
| 9 | books/textbook/chap3/programs.lisp | 36 | Basic program definitions |
| 10 | books/textbook/chap11/how-many-soln1.lisp | 32 | Counting problem (approach 1) |
| 11 | books/textbook/chap11/summations-book.lisp | 32 | Summation book support |
| 12 | books/textbook/chap11/xtr.lisp | 32 | Extraction/tree proofs |
| 13 | books/textbook/chap11/encap.lisp | 22 | Encapsulation patterns |
| 14 | books/textbook/chap11/mergesort.lisp | 22 | Mergesort verification |
| 15 | books/textbook/chap10/adder.lisp | 20 | Hardware adder verification |
| 16 | books/textbook/chap11/finite-sets.lisp | 20 | Finite set reasoning |
| 17 | books/textbook/chap11/perm.lisp | 20 | Permutation proofs |
| 18 | books/textbook/chap5/solutions.lisp | 19 | Chapter 5 solutions |
| 19 | books/textbook/chap10/compiler.lisp | 16 | Compiler verification |
| 20 | books/textbook/chap10/tree.lisp | 16 | Tree data structures |
| 21 | books/textbook/chap11/xtr2.lisp | 13 | More extraction proofs |
| 22 | books/textbook/chap10/insertion-sort.lisp | 12 | Insertion sort verification |
| 23 | books/textbook/chap11/starters.lisp | 23 | Starter lemmas |
| 24 | books/textbook/chap10/ac-example.lisp | 10 | AC rewriting example |
| 25 | books/textbook/chap11/perm-append.lisp | 9 | Perm-append proof |
| 26 | books/textbook/chap10/fact.lisp | 8 | Factorial verification |

### proofstyles/ — ALL 27 notebooks

Systematic comparison of proof methodologies: clock functions vs. invariant-based proofs,
partial vs. total correctness, soundness vs. completeness. Essential for understanding
when to use which proof technique.

| # | Notebook | Cells | Teaching Focus |
|---|---------|------:|----------------|
| 27 | books/proofstyles/invclock/Readme.lsp | 407 | **Master tutorial**: clock ↔ invariant |
| 28 | books/proofstyles/Readme.lsp | 125 | Overview of proof styles |
| 29 | books/proofstyles/invclock/c2i/c2i-total.lisp | 116 | Clock→invariant (total) |
| 30 | books/proofstyles/soundness/assertions-total.lisp | 100 | Assertions (total correctness) |
| 31 | books/proofstyles/completeness/assertions-total.lisp | 99 | Assertions completeness |
| 32 | books/proofstyles/soundness/assertions-partial.lisp | 84 | Assertions (partial correctness) |
| 33 | books/proofstyles/completeness/assertions-partial.lisp | 74 | Assertions completeness (partial) |
| 34 | books/proofstyles/invclock/i2c/i2c-partial.lisp | 43 | Invariant→clock (partial) |
| 35 | books/proofstyles/counterexamples/realistic.lisp | 39 | Realistic counterexamples |
| 36 | books/proofstyles/invclock/c2i/c2i-partial.lisp | 39 | Clock→invariant (partial) |
| 37 | books/proofstyles/invclock/i2c/i2c-total.lisp | 29 | Invariant→clock (total) |
| 38 | books/proofstyles/completeness/stepwise-invariants-total.lisp | 28 | Step invariants (total) |
| 39 | books/proofstyles/counterexamples/memory-clearing.lisp | 23 | Memory clearing |
| 40 | books/proofstyles/completeness/clock-partial.lisp | 19 | Clock proofs (partial) |
| 41 | books/proofstyles/completeness/clock-total.lisp | 19 | Clock proofs (total) |
| 42 | books/proofstyles/counterexamples/halt-flg.lisp | 18 | Halt flag examples |
| 43 | books/proofstyles/soundness/stepwise-invariants-partial.lisp | 17 | Step invariants (partial) |
| 44 | books/proofstyles/completeness/stepwise-invariants-partial.lisp | 16 | Step invariants partial |
| 45 | books/proofstyles/soundness/stepwise-invariants-total.lisp | 19 | Step invariants (total) |
| 46 | books/proofstyles/invclock/c2i/clock-to-inv.lisp | 15 | Conversion utilities |
| 47 | books/proofstyles/invclock/i2c/inv-to-clock.lisp | 15 | Conversion utilities |
| 48 | books/proofstyles/soundness/clock-total.lisp | 13 | Clock soundness |
| 49 | books/proofstyles/invclock/compose/compose-c-c-total.lisp | 11 | Composition (total) |
| 50 | books/proofstyles/invclock/compose/compose-c-c-partial.lisp | 10 | Composition (partial) |
| 51 | books/proofstyles/soundness/clock-partial.lisp | 10 | Clock soundness (partial) |
| 52 | books/proofstyles/completeness/generic-total.lisp | 3 | Generic total |
| 53 | books/proofstyles/completeness/generic-partial.lisp | 3 | Generic partial |

### demos/marktoberdorf-08/ — ALL 20 notebooks

J Strother Moore's Marktoberdorf Summer School 2008 lectures on ACL2.
Five lectures covering: ACL2 basics, the M1 machine model, operational semantics,
fast execution, compilation proof. This is the premier ACL2 tutorial material.

| # | Notebook | Cells | Teaching Focus |
|---|---------|------:|----------------|
| 54 | books/demos/marktoberdorf-08/preliminary-material.lisp | 107 | ACL2 fundamentals |
| 55 | books/demos/marktoberdorf-08/fast.lisp | 61 | Fast execution verification |
| 56 | books/demos/marktoberdorf-08/utilities.lisp | 50 | Utility library |
| 57 | books/demos/marktoberdorf-08/lecture1-input.lsp | 49 | Lecture 1: Basics |
| 58 | books/demos/marktoberdorf-08/m1.lisp | 45 | M1 machine model |
| 59 | books/demos/marktoberdorf-08/lecture3-input.lsp | 28 | Lecture 3: Verification |
| 60 | books/demos/marktoberdorf-08/lecture4-input.lsp | 25 | Lecture 4: Advanced |
| 61 | books/demos/marktoberdorf-08/lecture2-input.lsp | 20 | Lecture 2: Induction |
| 62 | books/demos/marktoberdorf-08/compile.lisp | 19 | Compiler proof |
| 63 | books/demos/marktoberdorf-08/g-invariant.lisp | 17 | Guard invariant |
| 64 | books/demos/marktoberdorf-08/g-direct.lisp | 16 | Direct guard proof |
| 65 | books/demos/marktoberdorf-08/perm.lisp | 15 | Permutation example |
| 66 | books/demos/marktoberdorf-08/m1-fast.lisp | 14 | Fast M1 execution |
| 67 | books/demos/marktoberdorf-08/lecture5-input.lsp | 11 | Lecture 5: Wrapup |
| 68 | books/demos/marktoberdorf-08/lecture1-book.lisp | 3 | Book support |
| 69 | books/demos/marktoberdorf-08/lecture2-book.lisp | 3 | Book support |
| 70 | books/demos/marktoberdorf-08/lecture3-book.lisp | 3 | Book support |
| 71 | books/demos/marktoberdorf-08/lecture4-book.lisp | 3 | Book support |
| 72 | books/demos/marktoberdorf-08/lecture5-book.lisp | 3 | Book support |
| 73 | books/demos/marktoberdorf-08/m1-package.lsp | 2 | Package definition |

### Key demos/ — 27 selected notebooks

Selected from remaining demos for high teaching value: BRR debugging, proof techniques,
stobj patterns, and practical examples.

| # | Notebook | Cells | Teaching Focus |
|---|---------|------:|----------------|
| 74 | books/demos/brr-test-input.lsp | 171 | **BRR**: Break-rewrite-rule debugging |
| 75 | books/demos/brr-free-variables-input.lsp | 114 | BRR free variable investigation |
| 76 | books/demos/loop-primer/lp17.lisp | 117 | Loop verification (comprehensive) |
| 77 | books/demos/congruent-stobjs-input.lsp | 91 | Congruent stobjs pattern |
| 78 | books/demos/big-proof-talks/talk2-input.lsp | 82 | Big proof methodology |
| 79 | books/demos/defabsstobj-example-1-df.lisp | 68 | Abstract stobj (data form) |
| 80 | books/demos/defabsstobj-example-1.lisp | 65 | Abstract stobj examples |
| 81 | books/demos/defabsstobj-example-5.lisp | 66 | Abstract stobj (advanced) |
| 82 | books/demos/geneqv-test-input.lsp | 54 | Generalized equivalence |
| 83 | books/demos/defabsstobj-example-3.lisp | 54 | Abstract stobj (patterns) |
| 84 | books/demos/defabsstobj-example-2.lisp | 51 | Abstract stobj (more) |
| 85 | books/demos/defabsstobj-example-4-input.lsp | 47 | Abstract stobj (interactive) |
| 86 | books/demos/loop-primer/lp12.lisp | 46 | Loop primer chapter 12 |
| 87 | books/demos/attach-stobj/demo-input.lsp | 42 | Stobj attachment |
| 88 | books/demos/loop-primer/lp8.lisp | 36 | Loop primer chapter 8 |
| 89 | books/demos/ld-history-input.lsp | 35 | LD history tracking |
| 90 | books/demos/fp/fp.lisp | 31 | Floating point reasoning |
| 91 | books/demos/big-proof-talks/talk1-input.lsp | 28 | Big proof talk 1 |
| 92 | books/demos/loop-primer/lp17-11-lemma2.lisp | 28 | Loop lemma proof |
| 93 | books/demos/divp-by-casting.lisp | 26 | Divisibility by casting |
| 94 | books/demos/loop-primer/lp14.lisp | 24 | Loop primer chapter 14 |
| 95 | books/demos/loop-primer/lp6.lisp | 24 | Loop primer chapter 6 |
| 96 | books/demos/knuth-bendix-problem-1.lisp | 21 | Knuth-Bendix completion |
| 97 | books/demos/list-equality-from-nth.lisp | 20 | List equality technique |
| 98 | books/demos/gl-and-use-example.lisp | 19 | GL bit-blasting + USE |
| 99 | books/demos/majority-vote.lisp | 14 | Majority vote verification |
| 100 | books/demos/list-theory.lisp | 12 | List theory utilities |

**Batch 2 Total: 100 notebooks**

---

## Batch 3: Hints, Clause Processors & Proof Debugging (~100 notebooks)

**Rationale**: Getting proofs to succeed is the #1 challenge for ACL2 users. This batch
covers the hint mechanism, clause processors (automated proof extensions), and key
proof debugging tools. An ACL2 coding agent must know how to provide hints, use
clause processors, and diagnose proof failures.

### hints/ — 20 key notebooks

Hints guide ACL2's prover: `:use`, `:in-theory`, `:expand`, computed hints,
consider hints, and more. `basic-tests.lisp` (163 cells) is the comprehensive
reference for all hint types.

| # | Notebook | Cells | Teaching Focus |
|---|---------|------:|----------------|
| 1 | books/hints/basic-tests.lisp | 163 | **All hint types**: comprehensive |
| 2 | books/hints/huet-lang-algorithm.lisp | 134 | Huet-Lang unification hints |
| 3 | books/hints/consider-hint-tests.lisp | 85 | Consider hint usage |
| 4 | books/hints/consider-hint.lisp | 42 | Consider hint implementation |
| 5 | books/hints/huet-lang-algorithm-tests.lisp | 39 | Unification tests |
| 6 | books/hints/subgoalp.lisp | 25 | Subgoal identification |
| 7 | books/hints/merge-hint.lisp | 20 | Hint merging |
| 8 | books/hints/use-pkg.lisp | 15 | Package-aware hints |
| 9 | books/hints/hint-wrapper.lisp | 14 | Hint wrapping patterns |
| 10 | books/kestrel/hints/renaming.lisp | 30 | Hint renaming |
| 11 | books/kestrel/hints/remove-hints-tests.lisp | 23 | Remove hints examples |
| 12 | books/kestrel/hints/renaming-tests.lisp | 18 | Renaming tests |
| 13 | books/kestrel/hints/remove-hints.lisp | 18 | Remove hints |
| 14 | books/kestrel/hints/combine-hints-tests.lisp | 17 | Combining hints |
| 15 | books/kestrel/hints/combine-hints.lisp | 16 | Hint combination |
| 16 | books/kestrel/hints/top.lisp | 10 | Top-level includes |
| 17 | books/kestrel/hints/casesx-tests.lisp | 10 | Case split hints |
| 18 | books/kestrel/hints/goal-specs.lisp | 9 | Goal specification |
| 19 | books/kestrel/hints/casesx.lisp | 8 | Case split implementation |
| 20 | books/hints/Readme.lsp | 2 | Overview |

### clause-processors/ — 30 selected notebooks

Clause processors extend ACL2's prover with custom proof procedures. Essential for
SAT solving, symbolic execution, bit-vector reasoning, and more.

| # | Notebook | Cells | Teaching Focus |
|---|---------|------:|----------------|
| 21 | books/clause-processors/basic-examples.lisp | 210 | **Core**: all CP patterns |
| 22 | books/clause-processors/SULFA/books/sat-tests/test-incremental.lisp | 238 | SAT incremental |
| 23 | books/clause-processors/SULFA/books/bv-smt-solver/bv-lib-definitions.lisp | 119 | BV SMT solver |
| 24 | books/clause-processors/SULFA/books/sat/convert-to-cnf.lisp | 130 | CNF conversion |
| 25 | books/clause-processors/SULFA/books/sat/sat.lisp | 97 | SAT solver |
| 26 | books/clause-processors/SULFA/books/sat/sat-setup.lisp | 87 | SAT setup |
| 27 | books/clause-processors/let-abstraction.lisp | 60 | Let abstraction CP |
| 28 | books/clause-processors/just-expand.lisp | 56 | Just-expand CP |
| 29 | books/clause-processors/constant-prop.lisp | 56 | Constant propagation |
| 30 | books/clause-processors/bindinglist.lisp | 56 | Binding list utilities |
| 31 | books/clause-processors/SULFA/books/sat/recognizer.lisp | 53 | Formula recognizer |
| 32 | books/clause-processors/SULFA/books/sat-tests/tutorial.lisp | 47 | **SAT tutorial** |
| 33 | books/clause-processors/induction.lisp | 47 | Induction CP |
| 34 | books/clause-processors/SULFA/books/bv-smt-solver/translation.lisp | 45 | BV translation |
| 35 | books/clause-processors/SULFA/books/bv-smt-solver/redundancy-removal.lisp | 42 | Redundancy |
| 36 | books/clause-processors/autohide.lisp | 39 | Auto-hiding CP |
| 37 | books/clause-processors/equality.lisp | 39 | Equality reasoning |
| 38 | books/clause-processors/generalize.lisp | 38 | Generalization CP |
| 39 | books/clause-processors/bv-add.lisp | 38 | BV addition |
| 40 | books/clause-processors/SULFA/books/sat/check-output.lisp | 36 | SAT output check |
| 41 | books/clause-processors/SULFA/books/clause-processors/sat-clause-processor.lisp | 29 | SAT clause proc |
| 42 | books/clause-processors/decomp-hint.lisp | 26 | Decomposition hints |
| 43 | books/clause-processors/bv-add-tests.lisp | 25 | BV addition tests |
| 44 | books/clause-processors/eval-alist-equiv.lisp | 24 | Evaluator alist |
| 45 | books/clause-processors/SULFA/books/sat/neq-implication.lisp | 25 | NEQ implication |
| 46 | books/clause-processors/SULFA/books/bv-smt-solver/bv-lib-lemmas.lisp | 21 | BV lemmas |
| 47 | books/clause-processors/SULFA/books/sat-tests/sudoku.lisp | 21 | **Sudoku solver** |
| 48 | books/clause-processors/SULFA/books/bv-smt-solver/smt.lisp | 20 | SMT interface |
| 49 | books/clause-processors/instantiate.lisp | 19 | Instantiation CP |
| 50 | books/clause-processors/ev-find-rules.lisp | 16 | Rule finding |

### std/util/ — 30 selected notebooks (definition macros)

The `std/util` library provides `define`, `defmapping`, `defenum`, `defval`, and other
macros critical for idiomatic ACL2 programming. An agent must know these to write
modern ACL2 code.

| # | Notebook | Cells | Teaching Focus |
|---|---------|------:|----------------|
| 51 | books/std/util/defmapping-tests-validation.lisp | 123 | Mapping validation |
| 52 | books/std/util/define.lisp | 96 | **Core**: `define` macro |
| 53 | books/std/util/defmapping.lisp | 91 | `defmapping` macro |
| 54 | books/std/util/defmapping-tests-template-2-2.lisp | 87 | Mapping template tests |
| 55 | books/std/util/defmapping-tests-template-1-1.lisp | 79 | Mapping template tests |
| 56 | books/std/util/defmapping-tests-template-2-1.lisp | 75 | Mapping template tests |
| 57 | books/std/util/da-base.lisp | 42 | Defaggregate base |
| 58 | books/std/util/defmapping-tests-utils.lisp | 35 | Mapping test utils |
| 59 | books/std/util/bstar.lisp | 31 | **b* binding form** |
| 60 | books/std/util/defval.lisp | 24 | `defval` macro |
| 61 | books/std/util/defenum.lisp | 22 | `defenum` macro |
| 62 | books/std/util/defconsts.lisp | 22 | `defconsts` macro |
| 63 | books/std/util/defrule.lisp | 19 | `defrule` macro |
| 64 | books/std/util/support.lisp | 18 | Support utilities |
| 65 | books/std/util/defret-mutual-generate.lisp | 17 | Mutual defret |
| 66 | books/std/util/returnspecs.lisp | 16 | Return specifications |
| 67 | books/std/util/deflist.lisp | 15 | `deflist` macro |
| 68 | books/std/util/defalist.lisp | 14 | `defalist` macro |
| 69 | books/std/util/defines.lisp | 14 | `defines` (mutual recursion) |
| 70 | books/std/util/defprojection.lisp | 14 | `defprojection` macro |
| 71 | books/std/util/wizard.lisp | 13 | Configuration wizard |
| 72 | books/std/util/defund-sk.lisp | 12 | `defund-sk` (quantified) |
| 73 | books/std/util/defmvtypes.lisp | 12 | Multi-value types |
| 74 | books/std/util/defarbrec.lisp | 11 | Arbitrary recursion |
| 75 | books/std/util/defmax-nat.lisp | 10 | Max-nat macro |
| 76 | books/std/util/defmin-int.lisp | 10 | Min-int macro |
| 77 | books/std/util/defaggregate.lisp | 9 | `defaggregate` macro |
| 78 | books/std/util/defredundant.lisp | 9 | Redundant definitions |
| 79 | books/std/util/defthm-domain.lisp | 8 | Domain theorems |
| 80 | books/std/util/top.lisp | 9 | Top-level includes |

### data-structures/ — 10 key notebooks

Core data structure implementations with extensive theorem coverage.

| # | Notebook | Cells | Teaching Focus |
|---|---------|------:|----------------|
| 81 | books/data-structures/list-defthms.lisp | 251 | **Comprehensive list theorems** |
| 82 | books/data-structures/structures.lisp | 200 | Structure definitions |
| 83 | books/data-structures/alist-defthms.lisp | 175 | Alist theorems |
| 84 | books/data-structures/deflist.lisp | 151 | List type definitions |
| 85 | books/data-structures/memories/memory-impl.lisp | 107 | Memory implementation |
| 86 | books/data-structures/memories/memtree.lisp | 90 | Memory tree |
| 87 | books/data-structures/array1.lisp | 69 | Array implementation |
| 88 | books/data-structures/defalist.lisp | 67 | Alist type definitions |
| 89 | books/data-structures/memories/memory.lisp | 62 | Memory interface |
| 90 | books/data-structures/alist-defuns.lisp | 60 | Alist functions |

### arithmetic-5/ — 10 key notebooks

The arithmetic-5 library provides automatic reasoning about arithmetic expressions.
Essential for any proof involving numbers.

| # | Notebook | Cells | Teaching Focus |
|---|---------|------:|----------------|
| 91 | books/arithmetic-5/lib/basic-ops/integerp.lisp | 466 | Integer type reasoning |
| 92 | books/arithmetic-5/lib/basic-ops/arithmetic-theory.lisp | 153 | Arithmetic theory |
| 93 | books/arithmetic-5/lib/basic-ops/simple-equalities-and-inequalities.lisp | 136 | Equalities/inequalities |
| 94 | books/arithmetic-5/lib/basic-ops/expt.lisp | 118 | Exponentiation |
| 95 | books/arithmetic-5/lib/basic-ops/building-blocks.lisp | 109 | Building blocks |
| 96 | books/arithmetic-5/lib/basic-ops/common.lisp | 106 | Common arithmetic |
| 97 | books/arithmetic-5/lib/basic-ops/collect.lisp | 93 | Term collection |
| 98 | books/arithmetic-5/lib/basic-ops/simplify.lisp | 79 | Simplification |
| 99 | books/arithmetic-5/lib/basic-ops/top.lisp | 73 | Top-level includes |
| 100 | books/arithmetic-5/lib/basic-ops/normalize.lisp | 67 | Normalization |

**Batch 3 Total: 100 notebooks**

---

## Batch 4: Standard Libraries — Lists, Alists, Strings, IO, Stobjs (~100 notebooks)

**Rationale**: These are the workhorse libraries that every ACL2 project uses daily.
List operations, alist manipulation, string processing, file I/O, and stobj (single-
threaded objects) are the building blocks of real ACL2 programs.

### std/lists/ — 45 selected notebooks

Core list operations with theorems. Every ACL2 program manipulates lists.

| # | Notebook | Cells | Teaching Focus |
|---|---------|------:|----------------|
| 1 | books/std/lists/sets.lisp | 55 | List-as-set operations |
| 2 | books/std/lists/top.lisp | 46 | Top-level includes |
| 3 | books/std/lists/list-defuns.lisp | 44 | Core list functions |
| 4 | books/std/lists/abstract.lisp | 34 | Abstract list operations |
| 5 | books/std/lists/no-duplicatesp.lisp | 15 | No duplicates predicate |
| 6 | books/std/lists/repeat.lisp | 13 | Repeat/replicate |
| 7 | books/std/lists/mfc-utils.lisp | 13 | Meta-function context |
| 8 | books/std/lists/nth.lisp | 12 | Nth element access |
| 9 | books/std/lists/take.lisp | 12 | Take prefix |
| 10 | books/std/lists/equiv.lisp | 11 | List equivalence |
| 11 | books/std/lists/remove-duplicates.lisp | 11 | Remove duplicates |
| 12 | books/std/lists/remove.lisp | 10 | Remove elements |
| 13 | books/std/lists/bits-equiv.lisp | 10 | Bit list equiv |
| 14 | books/std/lists/last.lisp | 10 | Last element |
| 15 | books/std/lists/rcons.lisp | 10 | Right-cons |
| 16 | books/std/lists/rev.lisp | 9 | Reverse |
| 17 | books/std/lists/flatten.lisp | 9 | List flattening |
| 18 | books/std/lists/nthcdr.lisp | 8 | Nthcdr operation |
| 19 | books/std/lists/duplicity.lisp | 8 | Duplicity counting |
| 20 | books/std/lists/intersection.lisp | 8 | List intersection |
| 21 | books/std/lists/intersectp.lisp | 8 | Intersection predicate |
| 22 | books/std/lists/nats-equiv.lisp | 8 | Nat-list equiv |
| 23 | books/std/lists/prefixp.lisp | 8 | Prefix predicate |
| 24 | books/std/lists/resize-list.lisp | 8 | List resizing |
| 25 | books/std/lists/set-difference.lisp | 8 | Set difference |
| 26 | books/std/lists/sublistp.lisp | 8 | Sublist predicate |
| 27 | books/std/lists/update-nth.lisp | 8 | Update at position |
| 28 | books/std/lists/union.lisp | 8 | List union |
| 29 | books/std/lists/acl2-count.lisp | 8 | ACL2 count |
| 30 | books/std/lists/subseq.lisp | 7 | Subsequence |
| 31 | books/std/lists/butlast.lisp | 7 | Butlast operation |
| 32 | books/std/lists/reverse.lisp | 7 | Reverse theorems |
| 33 | books/std/lists/true-listp.lisp | 7 | True-listp |
| 34 | books/std/lists/list-fix.lisp | 6 | List fixing |
| 35 | books/std/lists/add-to-set.lisp | 6 | Add to set |
| 36 | books/std/lists/append.lisp | 6 | Append theorems |
| 37 | books/std/lists/final-cdr.lisp | 6 | Final cdr |
| 38 | books/std/lists/revappend.lisp | 6 | Reverse-append |
| 39 | books/std/lists/remove1-equal.lisp | 6 | Remove first equal |
| 40 | books/std/lists/all-equalp.lisp | 10 | All-equal predicate |
| 41 | books/std/lists/suffixp.lisp | 5 | Suffix predicate |
| 42 | books/std/lists/len.lisp | 5 | Length theorems |
| 43 | books/std/lists/same-lengthp.lisp | 5 | Same length pred |
| 44 | books/std/lists/index-of.lisp | 5 | Index-of |
| 45 | books/std/lists/acl2-customization.lsp | 4 | Customization |

### std/alists/ — 20 selected notebooks

Alists (association lists) are ACL2's primary key-value data structure. Fast alists
(hons-based) provide hash-table performance.

| # | Notebook | Cells | Teaching Focus |
|---|---------|------:|----------------|
| 46 | books/std/alists/alists-compatible.lisp | 36 | Alist compatibility |
| 47 | books/std/alists/top.lisp | 32 | Top-level includes |
| 48 | books/std/alists/alist-defuns.lisp | 32 | Core alist functions |
| 49 | books/std/alists/hons-put-assoc.lisp | 16 | Fast alist put |
| 50 | books/std/alists/put-assoc-equal.lisp | 16 | Put-assoc-equal |
| 51 | books/std/alists/abstract.lisp | 15 | Abstract operations |
| 52 | books/std/alists/alist-equiv.lisp | 13 | Alist equivalence |
| 53 | books/std/alists/alist-map-keys.lisp | 13 | Map over keys |
| 54 | books/std/alists/alist-map-vals.lisp | 13 | Map over values |
| 55 | books/std/alists/append-alist-keys.lisp | 13 | Append keys |
| 56 | books/std/alists/append-alist-vals.lisp | 13 | Append values |
| 57 | books/std/alists/strip-cdrs.lisp | 13 | Strip cdrs |
| 58 | books/std/alists/fast-alist-clean.lisp | 12 | Fast alist cleanup |
| 59 | books/std/alists/fal-all-boundp.lisp | 11 | All-bound predicate |
| 60 | books/std/alists/pairlis.lisp | 11 | Pairlis operation |
| 61 | books/std/alists/strip-cars.lisp | 10 | Strip cars |
| 62 | books/std/alists/alistp.lisp | 8 | Alistp predicate |
| 63 | books/std/alists/remove-assocs.lisp | 8 | Remove associations |
| 64 | books/std/alists/remove-assoc-equal.lisp | 8 | Remove by key |
| 65 | books/std/alists/hons-rassoc-equal.lisp | 7 | Reverse assoc |

### std/io/ — ALL 15 key notebooks

File I/O operations — reading/writing files, serialization. Important for any
ACL2 tool that processes files.

| # | Notebook | Cells | Teaching Focus |
|---|---------|------:|----------------|
| 66 | books/std/io/base.lisp | 52 | I/O base operations |
| 67 | books/std/io/read-ints.lisp | 47 | Integer reading |
| 68 | books/std/io/serialize-tests.lisp | 47 | Serialization tests |
| 69 | books/std/io/read-string-tests.lisp | 32 | String read tests |
| 70 | books/std/io/read-file-characters.lisp | 19 | File character reading |
| 71 | books/std/io/combine.lisp | 18 | I/O combining |
| 72 | books/std/io/print-objects.lisp | 18 | Object printing |
| 73 | books/std/io/read-file-bytes.lisp | 15 | Byte reading |
| 74 | books/std/io/serialize-tests2.lisp | 15 | More serialization |
| 75 | books/std/io/read-file-lines.lisp | 14 | Line-by-line reading |
| 76 | books/std/io/read-file-objects.lisp | 14 | Object reading |
| 77 | books/std/io/read-file-lines-no-newlines.lisp | 13 | Lines without newlines |
| 78 | books/std/io/open-channels.lisp | 12 | Channel management |
| 79 | books/std/io/read-string.lisp | 10 | String reading |
| 80 | books/std/io/read-file-characters-no-error.lisp | 9 | Safe char reading |

### std/stobjs/ — 15 selected notebooks

Single-threaded objects (stobjs) are ACL2's mechanism for mutable state with
logical soundness. Essential for efficient programs.

| # | Notebook | Cells | Teaching Focus |
|---|---------|------:|----------------|
| 81 | books/std/stobjs/nicestobj.lisp | 34 | Nice stobj patterns |
| 82 | books/std/stobjs/updater-independence.lisp | 32 | Updater independence |
| 83 | books/std/stobjs/2d-arr.lisp | 30 | 2D arrays |
| 84 | books/std/stobjs/tests/def-hash.lisp | 29 | Hash table tests |
| 85 | books/std/stobjs/def-hash-theory.lisp | 23 | Hash table theory |
| 86 | books/std/stobjs/clone.lisp | 22 | Stobj cloning |
| 87 | books/std/stobjs/stobjtab.lisp | 18 | Stobj tables |
| 88 | books/std/stobjs/1d-arr.lisp | 13 | 1D arrays |
| 89 | books/std/stobjs/tests/2d-arr.lisp | 14 | 2D array tests |
| 90 | books/std/stobjs/tests/1d-arr.lisp | 12 | 1D array tests |
| 91 | books/std/stobjs/def-hash.lisp | 11 | Hash table defs |
| 92 | books/std/stobjs/natarr.lisp | 10 | Natural number arrays |
| 93 | books/std/stobjs/tests/clone.lisp | 10 | Clone tests |
| 94 | books/std/stobjs/nested-stobjs.lisp | 9 | Nested stobjs |
| 95 | books/std/stobjs/top.lisp | 10 | Top-level includes |

### std/bitsets/ — 5 key notebooks

Bitset operations for efficient set representations.

| # | Notebook | Cells | Teaching Focus |
|---|---------|------:|----------------|
| 96 | books/std/bitsets/sbitsets.lisp | 57 | Sparse bitsets |
| 97 | books/std/bitsets/bitsets.lisp | 33 | Core bitset operations |
| 98 | books/std/bitsets/bits-between.lisp | 24 | Bit range extraction |
| 99 | books/std/bitsets/bignum-extract-opt-tests.lisp | 15 | Bignum tests |
| 100 | books/std/bitsets/bitsets-tests.lisp | 9 | Bitset tests |

**Batch 4 Total: 100 notebooks**

---

## Batch 5: Advanced Tools, APT & Projects (~100 notebooks)

**Rationale**: This batch covers the Kestrel APT program transformation toolkit,
the codewalker project (symbolic execution/decompilation), and selected high-value
project notebooks demonstrating real-world ACL2 usage patterns.

### kestrel/apt/ — 30 selected notebooks

APT (Automated Program Transformations) provides verified program transformations:
restrict, parteval, casesplit, isodata, expdata, finite-difference, etc.

| # | Notebook | Cells | Teaching Focus |
|---|---------|------:|----------------|
| 1 | books/kestrel/apt/isodata.lisp | 252 | Isomorphic data transform |
| 2 | books/kestrel/apt/expdata.lisp | 227 | Export data transform |
| 3 | books/kestrel/apt/propagate-iso.lisp | 139 | Propagate isomorphism |
| 4 | books/kestrel/apt/isodata-tests.lisp | 101 | Isodata examples |
| 5 | books/kestrel/apt/casesplit.lisp | 82 | Case splitting |
| 6 | books/kestrel/apt/parteval.lisp | 80 | Partial evaluation |
| 7 | books/kestrel/apt/lift-iso.lisp | 76 | Lift isomorphism |
| 8 | books/kestrel/apt/restrict.lisp | 65 | Domain restriction |
| 9 | books/kestrel/apt/drop-irrelevant-params.lisp | 56 | Drop parameters |
| 10 | books/kestrel/apt/parteval-tests.lisp | 52 | Parteval examples |
| 11 | books/kestrel/apt/restrict-tests.lisp | 51 | Restrict examples |
| 12 | books/kestrel/apt/rename-params.lisp | 41 | Parameter renaming |
| 13 | books/kestrel/apt/doc.lisp | 39 | APT documentation |
| 14 | books/kestrel/apt/schemalg-divconq-list-0-1-template-tests.lisp | 37 | Divide & conquer |
| 15 | books/kestrel/apt/schemalg-divconq-oset-0-1-template-tests.lisp | 37 | D&C with osets |
| 16 | books/kestrel/apt/drop-irrelevant-params-tests.lisp | 35 | Drop params examples |
| 17 | books/kestrel/apt/propagate-iso-test-2.lisp | 33 | Propagate iso test |
| 18 | books/kestrel/apt/propagate-iso-test-3.lisp | 33 | Propagate iso test |
| 19 | books/kestrel/apt/propagate-iso-test-1b.lisp | 32 | Propagate iso test |
| 20 | books/kestrel/apt/finite-difference.lisp | 28 | Finite difference |
| 21 | books/kestrel/apt/propagate-iso-test-1.lisp | 28 | Propagate iso test |
| 22 | books/kestrel/apt/finite-difference-tests.lisp | 27 | FD examples |
| 23 | books/kestrel/apt/rename-params-tests.lisp | 27 | Rename tests |
| 24 | books/kestrel/apt/propagate-iso-test-4.lisp | 27 | Propagate iso test |
| 25 | books/kestrel/apt/lift-iso-tests.lisp | 22 | Lift iso examples |
| 26 | books/kestrel/apt/rename-calls-tests.lisp | 16 | Rename calls tests |
| 27 | books/kestrel/apt/casesplit-tests.lisp | 15 | Casesplit examples |
| 28 | books/kestrel/apt/schemalg-concrete-tests.lisp | 15 | Schema examples |
| 29 | books/kestrel/apt/common-concepts.lisp | 13 | Shared concepts |
| 30 | books/kestrel/apt/common-options.lisp | 13 | Shared options |

### projects/codewalker/ — ALL 14 notebooks

Codewalker provides symbolic execution and decompilation for the M1 JVM-like machine.
Demonstrates how to reason about bytecode programs in ACL2.

| # | Notebook | Cells | Teaching Focus |
|---|---------|------:|----------------|
| 31 | books/projects/codewalker/terminatricks.lisp | 212 | Termination tricks |
| 32 | books/projects/codewalker/codewalker.lisp | 190 | Core codewalker |
| 33 | books/projects/codewalker/m1-version-3.lisp | 85 | M1 machine v3 |
| 34 | books/projects/codewalker/basic-demo.lsp | 69 | Basic demo |
| 35 | books/projects/codewalker/demo-fact-partial.lisp | 60 | Factorial (partial) |
| 36 | books/projects/codewalker/demo-fact-count-up.lisp | 39 | Count-up factorial |
| 37 | books/projects/codewalker/simplify-under-hyps.lisp | 30 | Simplification |
| 38 | books/projects/codewalker/demo-fact-preamble.lisp | 22 | Factorial preamble |
| 39 | books/projects/codewalker/demo-fact.lisp | 18 | Factorial demo |
| 40 | books/projects/codewalker/if-tracker.lisp | 12 | If-tracking |
| 41 | books/projects/x86isa/proofs/codewalker-examples/factorial.lisp | 28 | x86 factorial |
| 42 | books/projects/x86isa/proofs/codewalker-examples/popcount-32.lisp | 18 | x86 popcount |
| 43 | books/projects/x86isa/proofs/codewalker-examples/base.lisp | 12 | x86 codewalker base |
| 44 | books/projects/x86isa/proofs/codewalker-examples/acl2-customization.lsp | 7 | x86 customization |

### projects/abnf/ — 15 selected notebooks

ABNF grammar parsing — demonstrates building verified parsers in ACL2.

| # | Notebook | Cells | Teaching Focus |
|---|---------|------:|----------------|
| 45 | books/projects/abnf/grammar-parser/verification.lisp | 499 | Parser verification |
| 46 | books/projects/abnf/grammar-definer/deftreeops.lisp | 160 | Tree operations |
| 47 | books/projects/abnf/parsing-tools/defdefparse.lisp | 120 | Parser definition macro |
| 48 | books/projects/abnf/grammar-parser/executable.lisp | 84 | Executable parser |
| 49 | books/projects/abnf/grammar-definer/defgrammar.lisp | 51 | Grammar definition |
| 50 | books/projects/abnf/constructor-utilities.lisp | 16 | Constructors |
| 51 | books/projects/abnf/examples/uri.lisp | 16 | URI grammar |
| 52 | books/projects/abnf/parsing-tools/primitives-defresult.lisp | 19 | Parser primitives |
| 53 | books/projects/abnf/examples/http.lisp | 15 | HTTP grammar |
| 54 | books/projects/abnf/examples/imap.lisp | 15 | IMAP grammar |
| 55 | books/projects/abnf/examples/imf.lisp | 15 | IMF (email) grammar |
| 56 | books/projects/abnf/parsing-tools/primitives-seq.lisp | 16 | Sequence primitives |
| 57 | books/projects/abnf/examples/smtp.lisp | 13 | SMTP grammar |
| 58 | books/projects/abnf/examples/top.lisp | 11 | Examples top |
| 59 | books/projects/abnf/examples/pdf.lisp | 11 | PDF grammar |

### centaur/misc/ — 20 selected notebooks (key utilities)

Important utility libraries from the Centaur team: context rewriting, bound rewriting,
graph algorithms, evaluator metatheorems.

| # | Notebook | Cells | Teaching Focus |
|---|---------|------:|----------------|
| 60 | books/centaur/misc/bound-rewriter.lisp | 135 | Bound rewriting |
| 61 | books/centaur/misc/context-rw.lisp | 93 | Context-sensitive rewriting |
| 62 | books/centaur/misc/defapply.lisp | 71 | Define-apply |
| 63 | books/centaur/misc/dag-measure-thms.lisp | 64 | DAG measure theorems |
| 64 | books/centaur/misc/hons-sets.lisp | 55 | Hons-based sets |
| 65 | books/centaur/misc/collect-like-terms.lisp | 55 | Like-term collection |
| 66 | books/centaur/misc/dfs-seen-property.lisp | 53 | DFS properties |
| 67 | books/centaur/misc/interp-function-lookup.lisp | 47 | Interpretation |
| 68 | books/centaur/misc/intstack.lisp | 45 | Integer stack |
| 69 | books/centaur/misc/alist-canonicalize.lisp | 41 | Alist canonicalization |
| 70 | books/centaur/misc/def-bounds.lisp | 41 | Bound definitions |
| 71 | books/centaur/misc/evaluator-metatheorems.lisp | 40 | Meta-theorems |
| 72 | books/centaur/misc/alphanum-sort.lisp | 34 | Sort implementation |
| 73 | books/centaur/misc/graphviz.lisp | 32 | Graphviz output |
| 74 | books/centaur/misc/fast-alists.lisp | 30 | Fast alist patterns |
| 75 | books/centaur/misc/dfs-measure.lisp | 30 | DFS measure |
| 76 | books/centaur/misc/iter.lisp | 30 | Iterator patterns |
| 77 | books/centaur/misc/introduce-var.lisp | 27 | Variable introduction |
| 78 | books/centaur/misc/beta-reduce-full.lisp | 23 | Beta reduction |
| 79 | books/centaur/misc/hons-extra.lisp | 23 | Hons extensions |

### coi/ — 11 selected notebooks (key libraries)

The COI (Community of Interest) libraries provide bags, paths, records, and other
useful abstractions.

| # | Notebook | Cells | Teaching Focus |
|---|---------|------:|----------------|
| 80 | books/coi/bags/basic.lisp | 468 | **Bag theory** (comprehensive) |
| 81 | books/coi/bags/eric-meta.lisp | 231 | Meta reasoning for bags |
| 82 | books/coi/bags/meta.lisp | 191 | Bag meta-functions |
| 83 | books/coi/bags/bind-free-rules.lisp | 103 | Bind-free rule patterns |
| 84 | books/coi/alists/keyquiv.lisp | 96 | Key equivalence |
| 85 | books/coi/alists/bindequiv.lisp | 69 | Binding equivalence |
| 86 | books/coi/alists/equiv.lisp | 63 | Alist equivalence |
| 87 | books/coi/adviser/adviser.lisp | 47 | Proof adviser |
| 88 | books/coi/bags/two-level.lisp | 37 | Two-level bags |
| 89 | books/coi/alists/subkeyquiv.lisp | 36 | Sub-key equivalence |
| 90 | books/coi/alists/strip.lisp | 35 | Alist stripping |

### std/typed-lists/ — 10 key notebooks

Typed list recognizers and theorems.

| # | Notebook | Cells | Teaching Focus |
|---|---------|------:|----------------|
| 91 | books/std/typed-lists/top.lisp | 18 | Top-level includes |
| 92 | books/std/typed-lists/unsigned-byte-listp.lisp | 8 | Unsigned byte lists |
| 93 | books/std/typed-lists/signed-byte-listp.lisp | 7 | Signed byte lists |
| 94 | books/std/typed-lists/nat-listp.lisp | 7 | Natural number lists |
| 95 | books/std/typed-lists/boolean-listp.lisp | 6 | Boolean lists |
| 96 | books/std/typed-lists/character-listp.lisp | 6 | Character lists |
| 97 | books/std/typed-lists/integer-listp.lisp | 6 | Integer lists |
| 98 | books/std/typed-lists/string-listp.lisp | 6 | String lists |
| 99 | books/std/typed-lists/symbol-listp.lisp | 6 | Symbol lists |
| 100 | books/std/typed-lists/acl2-number-listp.lisp | 6 | Number lists |

**Batch 5 Total: 100 notebooks**

---

## Summary

| Batch | Theme | Notebooks | Key Focus |
|-------|-------|----------:|-----------|
| 1 | Foundation: FTY, Basic Types, Osets, Ordinals | 100 | Type system, fix/equiv patterns, termination |
| 2 | Teaching: Textbook, Proofstyles, Lectures, Demos | 100 | Proof methodology, worked examples, BRR debugging |
| 3 | Hints, Clause Processors, Utilities, Arithmetic | 100 | Getting proofs to work, definition macros |
| 4 | Standard Libraries: Lists, Alists, IO, Stobjs | 100 | Data manipulation, I/O, mutable state |
| 5 | Advanced: APT, Codewalker, ABNF, Projects | 100 | Program transformation, verification projects |
| **Total** | | **500** | |

## Teaching Notebook Topics for the ACL2 Coding Agent

Based on this research, the agent should create teaching notebooks on these topics:

### Core Techniques
1. **FTY Type Definitions** — defprod, deftagsum, deflist, defalist, defoption, deftypes
2. **Fix/Equiv Pattern** — How every type has a fixer, equivalence, and fixing theorems
3. **Using `define`** — Modern function definition with guards, returns, b* binding
4. **Writing Recursive Functions** — Measures, termination, ordinals, well-foundedness
5. **Induction Schemes** — How ACL2 chooses induction, :induct hints, custom schemes

### Proof Strategies
6. **Hint Mechanisms** — :use, :in-theory, :expand, :cases, computed hints
7. **BRR Debugging** — Break-rewrite-rule, monitoring rewrite rules, free variables
8. **Accumulated Persistence** — Identifying expensive/useless rules
9. **Checkpoint Analysis** — Reading failed proof checkpoints, identifying subgoals
10. **Proof Styles** — Clock functions vs invariants, partial vs total correctness
11. **Clause Processors** — Writing and using custom clause processors
12. **SAT/SMT Integration** — Using SULFA, GL, FGL for decidable reasoning

### Libraries
13. **List Operations** — std/lists patterns: append, nth, take, rev, member
14. **Alist Patterns** — Association lists, fast alists, hons-acons
15. **Ordered Sets** — std/osets: insert, delete, intersect, union, merge
16. **Stobj Patterns** — Defining stobjs, abstract stobjs, congruent stobjs
17. **String Processing** — std/strings: concatenation, parsing, conversion
18. **File I/O** — Reading/writing files, serialization
19. **Bitset Operations** — Efficient bit-level set operations
20. **Arithmetic Reasoning** — arithmetic-5, linear arithmetic, non-linear

### Advanced Patterns
21. **APT Transformations** — restrict, parteval, casesplit, isodata, expdata
22. **Codewalker** — Symbolic execution, program decompilation
23. **Data Structures** — Arrays, records, bags, memory models
24. **Meta-functions** — Writing meta-level reasoning rules
25. **Encapsulation** — Functional instantiation, generic theories

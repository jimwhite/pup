# ACL2 Agent Instructions: Proving Convergence of the Coin-Toss π Estimator

## Overview

This document provides structured instructions for an ACL2 coding agent to formally verify the convergence of Jim Propp's coin-tossing method for estimating π. The method, described in the paper "Estimating π with a Coin" (arXiv:2602.14487, submitted February 2026), gives a surprising probabilistic interpretation of π/4: toss a fair coin repeatedly until the cumulative count of heads first exceeds the cumulative count of tails, record the proportion of heads at that stopping time, and repeat; the average of those proportions converges to π/4. The convergence was also popularized via Matt Parker's YouTube video and James Propp's Mathematical Enchantments blog.[^1][^2]

The mathematical proof is complete and peer-reviewed. The task for the ACL2 agent is to formalize it mechanically. This document covers the mathematical structure, the decomposition into lemmas, the appropriate ACL2 variant to use, relevant library books, and an annotated proof skeleton.

***

## The Mathematical Method

### Setup and Notation

Let \(H_n\) and \(T_n\) denote the cumulative number of heads and tails, respectively, after \(n\) independent fair coin tosses. Define the random walk[^2]

\[ S_n = H_n - T_n \]

so that \((S_n)\) is a simple symmetric random walk on \(\mathbb{Z}\) starting at \(S_0 = 0\), moving \(+1\) on heads and \(-1\) on tails. The **stopping time** is[^2]

\[ \tau = \min\{n \geq 1 : S_n > 0\} \]

i.e., the first time the walk hits \(+1\). Since \(S_n\) changes by exactly \(\pm 1\) at each step and must first cross zero by reaching \(+1\), \(\tau\) is always an **odd** positive integer: \(\tau \in \{1, 3, 5, 7, \ldots\}\).[^2]

### Stopping-Time Distribution via Catalan Numbers

For \(\tau = 2m - 1\) (with \(m \geq 1\)) to occur, the walk must stay \(\leq 0\) for the first \(2m - 2\) steps, be at position \(0\) at step \(2m - 2\), then step to \(+1\). The number of such paths is the \((m-1)\)-th Catalan number[^2]

\[ C_{m-1} = \frac{1}{m}\binom{2(m-1)}{m-1} \]

Each path of length \(2m - 1\) has probability \(2^{-(2m-1)}\), yielding[^2]

\[ P(\tau = 2m-1) = \frac{C_{m-1}}{2^{2m-1}}, \quad m = 1, 2, 3, \ldots \]

### Key Algebraic Reduction

At stopping time \(\tau\), the conditions \(H_\tau - T_\tau = 1\) and \(H_\tau + T_\tau = \tau\) together give \(H_\tau = (\tau + 1)/2\), and thus[^2]

\[ \frac{H_\tau}{\tau} = \frac{1}{2} + \frac{1}{2\tau} \]

Taking expectations:

\[ E\!\left[\frac{H_\tau}{\tau}\right] = \frac{1}{2} + \frac{1}{2} E\!\left[\frac{1}{\tau}\right] \quad \text{[Eq. 1]} \]

The problem reduces to computing \(E[1/\tau]\).

### Computing E[1/τ] via Arcsin Series

Using the Catalan distribution:[^2]

\[ E\!\left[\frac{1}{\tau}\right] = \sum_{m \geq 1} \frac{1}{2m-1} \cdot \frac{C_{m-1}}{2^{2m-1}} = \frac{1}{2} \sum_{k \geq 0} \frac{C_k}{(2k+1) \cdot 4^k} \]

Define \(A = \sum_{k \geq 0} \frac{C_k}{(2k+1) \cdot 4^k}\). Using the integral representation \(\frac{1}{k+1} = \int_0^1 t^k\, dt\) and Tonelli's theorem to interchange sum and integral:[^2]

\[ A = \int_0^1 \sum_{k \geq 0} \frac{\binom{2k}{k}}{(2k+1) \cdot 4^k} t^k\, dt \]

This uses the **arcsin Taylor series** \(\arcsin(x) = \sum_{k \geq 0} \frac{\binom{2k}{k}}{4^k(2k+1)} x^{2k+1}\)[^3], which converges for \(|x| \leq 1\)[^3]. Setting \(x = \sqrt{t}\) gives \(\frac{\arcsin(\sqrt{t})}{\sqrt{t}} = \sum_{k \geq 0} \frac{\binom{2k}{k}}{(2k+1) \cdot 4^k} t^k\), so

\[ A = \int_0^1 \frac{\arcsin(\sqrt{t})}{\sqrt{t}}\, dt \]

Substituting \(u = \sqrt{t}\) (so \(t = u^2\), \(dt = 2u\, du\)):

\[ A = 2 \int_0^1 \arcsin(u)\, du \]

### Integration by Parts

Let \(I = \int_0^1 \arcsin(u)\, du\). Integration by parts with \(f = \arcsin(u)\), \(g' = 1\):[^2]

\[ I = \left[u \arcsin(u)\right]_0^1 - \int_0^1 \frac{u}{\sqrt{1-u^2}}\, du = \frac{\pi}{2} - 1 \]

(the boundary term is \(\pi/2\); the remaining integral equals \(1\) via the substitution \(w = 1 - u^2\)).[^2]

### Final Result

\[ E\!\left[\frac{1}{\tau}\right] = \frac{A}{2} = I = \frac{\pi}{2} - 1 \]

Substituting into Eq. 1:

\[ E\!\left[\frac{H_\tau}{\tau}\right] = \frac{1}{2} + \frac{1}{2}\!\left(\frac{\pi}{2} - 1\right) = \frac{\pi}{4} \]

The Law of Large Numbers then guarantees that the sample mean of i.i.d. copies of \(H_\tau/\tau\) converges almost surely to \(\pi/4\).[^1]

***

## Choosing the Right ACL2 Variant

### The Core Challenge

Standard ACL2 is restricted to rationals and can prove that \(\sqrt{2}\) and \(\pi\) do not exist. Since the target theorem \(E[H_\tau/\tau] = \pi/4\) involves an irrational constant, the agent must work in **ACL2(r)**, the variant built on non-standard analysis that supports the full real and complex numbers.[^4][^5]

> **Important**: ACL2(r) proofs are incompatible with standard ACL2 books. An ACL2(r) proof environment cannot directly import standard ACL2 books and vice versa. The agent must commit to ACL2(r) for this proof.[^4]

### ACL2(r) Key Concepts

ACL2(r) is based on Non-Standard Analysis (NSA) à la Robinson. Relevant primitives the agent will use:[^5]

| Concept | ACL2(r) notation | Meaning |
|---|---|---|
| Standard real | `(standardp x)` | x is a "classical" real number |
| Infinitesimal | `(i-small x)` | x is infinitely close to 0 |
| Standard part | `(standard-part x)` | unique standard real i-close to x |
| i-close | `(i-close x y)` | x and y differ by an infinitesimal |
| Non-classical function def | `(defun-std ...)` | define classical function via NSA body |
| Overspill | `(overspill ...)` macro | apply overspill principle |

The agent must restrict non-classical recursive functions — they are **not** permitted in ACL2(r).[^6]

### Required Books

The agent should include the following at the top of the proof script:

```lisp
;; Core NSA infrastructure
(include-book "nonstd/nsa/nsa" :dir :system)

;; Transcendental functions (exp, ln, sin, cos, arcsin)
(include-book "nonstd/transcendentals/transcendentals" :dir :system)

;; Integration infrastructure (Riemann integral, FTC-1, FTC-2)
(include-book "nonstd/integrals/continuous-function" :dir :system)
(include-book "nonstd/integrals/ftc" :dir :system)

;; Arithmetic support
(include-book "arithmetic/top" :dir :system)
(include-book "arithmetic/binomial" :dir :system)
```

The Fourier series formalization in ACL2(r) provides a proven template for applying FTC-2 to functions with free arguments, as well as for infinite series integration via the overspill principle and Dini's theorem — both of which are required here.[^2]

***

## Proof Decomposition

The full proof is organized into **seven milestones**, each producing one or more `defthm` lemmas. The agent should complete them in order since later milestones depend on earlier ones.

***

### Milestone 1: Catalan Numbers

**Goal**: Define Catalan numbers and establish basic identities.

```lisp
;; C_k = (1/(k+1)) * C(2k, k)
(defun catalan (k)
  (declare (xargs :guard (natp k)))
  (/ (choose (* 2 k) k) (+ k 1)))

;; Verify a few base cases:
(defthm catalan-0 (equal (catalan 0) 1))
(defthm catalan-1 (equal (catalan 1) 1))
(defthm catalan-2 (equal (catalan 2) 2))
```

**Key lemma needed** — the integral representation \(1/(k+1) = \int_0^1 t^k\, dt\) which will be used in Milestone 4:

```lisp
(defthm catalan-integral-rep
  ;; C_k / (k+1) = integral_0^1 t^k dt * C(2k,k)
  ;; Established as a rewrite for use in series expansion
  ...)
```

**Key identity to prove** (ratio of consecutive Catalan numbers):

```lisp
(defthm catalan-ratio
  (implies (natp k)
           (equal (* (+ k 1) (catalan (+ k 1)))
                  (* (catalan k) (+ (* 2 k) 2))))
  ...)
```

***

### Milestone 2: Stopping-Time Distribution

**Goal**: Define the PMF of \(\tau\) and verify it is a valid probability distribution.

```lisp
;; P(tau = 2m-1) = catalan(m-1) / 2^(2m-1)
(defun tau-pmf (m)
  ;; m >= 1, returns probability of stopping at step 2m-1
  (declare (xargs :guard (and (natp m) (>= m 1))))
  (/ (catalan (- m 1))
     (expt 2 (- (* 2 m) 1))))
```

**Prove normalization** (the PMF sums to 1). This uses the generating function for Catalan numbers \(\sum_{k \geq 0} C_k x^k = (1 - \sqrt{1 - 4x})/(2x)\) at \(x = 1/4\):

```lisp
;; Partial sum bound (for all N):
(defthm tau-pmf-partial-sum-bound
  (implies (natp N)
           (<= (sum-tau-pmf 1 N) 1))
  ...)

;; Convergence of full sum to 1 (requires ACL2(r) standard-part):
(defthm tau-pmf-sums-to-one
  (i-close (sum-tau-pmf-infinite) 1)
  ...)
```

***

### Milestone 3: Algebraic Identity H_τ / τ = 1/2 + 1/(2τ)

**Goal**: Prove the deterministic identity relating the proportion of heads to the stopping time.

At stopping time \(\tau\), two conditions hold simultaneously: \(H_\tau + T_\tau = \tau\) (definition of total tosses) and \(H_\tau - T_\tau = 1\) (definition of stopping). These together imply \(H_\tau = (\tau + 1)/2\).

```lisp
(defthm heads-at-stopping-time
  ;; Given tau is odd and S_tau = 1:
  (implies (and (oddp tau)
                (equal (- heads tails) 1)
                (equal (+ heads tails) tau)
                (posp tau))
           (equal heads (/ (+ tau 1) 2)))
  :hints (("Goal" :arith t)))

(defthm proportion-formula
  ;; H_tau / tau = 1/2 + 1/(2*tau)
  (implies (and (posp tau)
                (equal heads (/ (+ tau 1) 2)))
           (equal (/ heads tau)
                  (+ 1/2 (/ 1 (* 2 tau)))))
  :hints (("Goal" :arith t)))
```

This milestone is **purely rational arithmetic** and requires no ACL2(r) features. It should be one of the easiest to complete.

***

### Milestone 4: The Catalan Series Equals A = π/2

**Goal**: Prove \(\sum_{k \geq 0} \frac{C_k}{(2k+1) \cdot 4^k} = \frac{\pi}{2}\).

This is the mathematical core of the proof and the hardest milestone. The argument uses:

1. **Tonelli's theorem** (interchange of sum and integral for non-negative functions),
2. The **arcsin Taylor series**, and
3. An explicit integral evaluation.

**Step 4a**: Establish the arcsin Taylor series in ACL2(r). The `books/nonstd/transcendentals` library provides `acl2-asin`. The agent needs:

```lisp
;; arcsin(x) = sum_{k>=0} C(2k,k)/(4^k * (2k+1)) * x^(2k+1)
;; Pointwise convergence for |x| <= 1 (already in nonstd books)
(defthm arcsin-taylor-series
  (implies (and (realp x) (<= (abs x) 1))
           (i-close (partial-arcsin-sum x N)
                    (acl2-asin x)))
  ...)
```

**Step 4b**: The series term at \(x = \sqrt{t}\). Using functional instantiation on the arcsin series:

```lisp
(defthm arcsin-series-at-sqrt-t
  (implies (and (realp t) (<= 0 t) (<= t 1))
           (i-close (/ (acl2-asin (acl2-sqrt t))
                       (acl2-sqrt t))
                    (partial-catalan-sum t N)))
  ...)
```

**Step 4c**: The series A as an integral. Apply the sum rule for integrals of infinite series (Dini uniform convergence):[^2]

```lisp
;; A = integral_0^1 arcsin(sqrt(t)) / sqrt(t) dt
(defthm catalan-series-as-integral
  (i-close (catalan-series-A)
           (integral-0-1 (lambda (t)
                           (/ (acl2-asin (acl2-sqrt t))
                              (acl2-sqrt t)))))
  ...)
```

**Step 4d**: Substitution \(u = \sqrt{t}\). This converts the integral to \(2 \int_0^1 \arcsin(u)\, du\):

```lisp
(defthm integral-substitution-sqrt
  (equal (integral-0-1 (lambda (t)
                         (/ (acl2-asin (acl2-sqrt t))
                            (acl2-sqrt t))))
         (* 2 (integral-0-1 (lambda (u) (acl2-asin u)))))
  :hints (("Goal" :use (:instance substitution-rule ...))))
```

***

### Milestone 5: Integration by Parts for ∫₀¹ arcsin(u) du

**Goal**: Prove \(\int_0^1 \arcsin(u)\, du = \pi/2 - 1\).

This is a two-part calculation using FTC-2:[^2]

**Step 5a**: Antiderivative of arcsin. The antiderivative of \(\arcsin(u)\) is \(u\arcsin(u) + \sqrt{1 - u^2}\). Use the automatic differentiator (`defderivative`) from the Fourier series book:[^2]

```lisp
(defderivative arcsin-antiderivative
  (+ (* u (acl2-asin u))
     (acl2-sqrt (- 1 (* u u)))))
;; This auto-generates proof that derivative = arcsin(u)
```

**Step 5b**: Apply FTC-2. Following the FTC-2 evaluation procedure for functions with free arguments:[^2]

```lisp
(defthm ftc2-arcsin
  (equal (integral-0-1 (lambda (u) (acl2-asin u)))
         (- (+ (* 1 (acl2-asin 1))
               (acl2-sqrt (- 1 (* 1 1))))
            (+ (* 0 (acl2-asin 0))
               (acl2-sqrt (- 1 (* 0 0))))))
  :hints (("Goal" :use (:functional-instance ftc-2 ...))))
```

**Step 5c**: Evaluate boundary values. Use the standard fact `(acl2-asin 1) = (/ (acl2-pi) 2)`:

```lisp
(defthm arcsin-at-one
  (equal (acl2-asin 1) (/ (acl2-pi) 2))
  ...)

(defthm arcsin-integral-value
  (equal (integral-0-1 (lambda (u) (acl2-asin u)))
         (- (/ (acl2-pi) 2) 1))
  ...)
```

***

### Milestone 6: E[1/τ] = π/2 − 1

**Goal**: Assemble Milestones 2–5 to prove the key expected value.

```lisp
;; E[1/tau] = sum_{m>=1} (1/(2m-1)) * tau-pmf(m)
(defun expected-inv-tau-partial (N)
  (sum-from-1-to-N m N
    (* (/ 1 (- (* 2 m) 1))
       (tau-pmf m))))

;; Prove convergence of partial sums:
(defthm expected-inv-tau-converges
  ;; Partial sums form a Cauchy sequence converging to A/2
  (i-close (expected-inv-tau-partial (+ N 1))
           (expected-inv-tau-partial N))
  ...)

;; Main result: E[1/tau] = pi/2 - 1
(defthm expected-inv-tau
  (i-close (standard-part (expected-inv-tau-partial (omega)))
           (- (/ (acl2-pi) 2) 1))
  :hints (("Goal" :use (catalan-series-as-integral
                        arcsin-integral-value))))
```

Here `(omega)` denotes a non-standard infinite natural, following the ACL2(r) idiom for infinite limits.[^5]

***

### Milestone 7: Main Theorem E[H_τ/τ] = π/4

**Goal**: Combine all prior milestones into the final theorem.

```lisp
;; E[H_tau/tau] = E[1/2 + 1/(2*tau)]
;;              = 1/2 + (1/2) * E[1/tau]
;;              = 1/2 + (1/2) * (pi/2 - 1)
;;              = pi/4

(defthm expected-proportion-is-pi-over-4
  (i-close (standard-part (expected-proportion-partial (omega)))
           (/ (acl2-pi) 4))
  :hints
  (("Goal"
    :use (proportion-formula
          expected-inv-tau
          linearity-of-expectation))))
```

**Convergence of Sample Mean** (Law of Large Numbers in ACL2(r)). For the Monte Carlo interpretation, the agent should also prove that the sample mean of \(n\) independent trials converges to \(\pi/4\):

```lisp
(defthm lln-for-proportion
  ;; For iid copies X_1, ..., X_N of H_tau/tau:
  ;; (X_1 + ... + X_N) / N -> pi/4 as N -> infinity
  (implies (and (natp N) (i-large N))
           (i-close (/ (sum-of-trials N)
                       N)
                    (/ (acl2-pi) 4)))
  :hints (("Goal" :use (expected-proportion-is-pi-over-4
                        weak-law-of-large-numbers))))
```

***

## Proof Strategy and Common Pitfalls

### Strategy Summary

| Milestone | Difficulty | ACL2(r) required? | Key technique |
|---|---|---|---|
| 1. Catalan numbers | Low | No | Induction, rational arithmetic |
| 2. Stopping time PMF | Medium | Yes (standard-part) | Generating functions, normalization |
| 3. Algebraic identity | Low | No | Linear arithmetic |
| 4. Catalan series = π/2 | High | Yes | Tonelli, arcsin series, FTC-2 |
| 5. ∫arcsin = π/2 − 1 | Medium | Yes | FTC-2, auto-differentiator |
| 6. E[1/τ] = π/2 − 1 | Medium | Yes | Combining series + integral |
| 7. Main theorem | Low | Yes | Algebraic combination |

### Non-Classical Recursion Restriction

ACL2(r) **does not permit** non-classical recursive functions. All recursive series and summation functions must be defined as *classical* functions (i.e., their bodies must not reference `standardp`, `i-small`, `i-close`, etc.). The non-classical properties of these functions are then *derived* by separate theorems.[^6]

### Free Arguments in Functional Instantiation

When applying FTC-2 or the sum rule for integration to functions with parameters (like the arcsin series parameterized by a truncation index), the direct approach of using pseudo-lambda terms with free arguments fails for non-classical theorems. The recommended workaround, established in the Fourier series formalization:[^2]
1. Wrap free arguments in a zero-arity `encapsulate` constant.
2. Prove the theorem with zero-arity constants.
3. Functionally instantiate to recover the result with the original free variable.

### Tonelli's Theorem

Interchanging sum and integral requires non-negativity of terms (Tonelli) or absolute convergence (Fubini). For the catalan series, all terms \(C_k / ((2k+1) \cdot 4^k)\) are non-negative, so Tonelli applies. The agent should establish non-negativity of terms as an explicit lemma before applying any interchange.

### Overspill for Infinite Sums

To assert that a series converges to a standard real value in ACL2(r), use the **overspill principle** with a predicate of the form "the partial sum is within ε of the limit". The overspill utility in `books/nonstd/nsa` automates this pattern.[^5][^2]

### Hints for Arithmetic Goals

ACL2(r) linear arithmetic handles rational inequalities but may need help with products and quotients involving \(\pi\). Use `:use` hints with explicit lemmas from the transcendentals book (e.g., `(acl2-pi-positive)`, `(acl2-pi-between-2-and-4)`).

***

## Expected Convergence Rate

As a sanity check, the mathematical analysis predicts that to obtain \(N\) correct decimal digits of \(\pi\), approximately \(10^{4N}\) coin tosses are needed. Parker's empirical test with 10,000 coin tosses (equivalent to \(N = 1\)) yielded an estimate of about 3.2, confirming one correct digit. The agent may optionally include a theorem bounding the convergence rate:[^1]

```lisp
;; After n trials, variance of sample mean = Var[H_tau/tau] / n
;; Typical error ~ 1 / sqrt(n)
;; For N decimal digits: n ~ 10^(4N) trials needed
(defthm convergence-rate-bound
  (implies (and (natp n) (< 0 n))
           (<= (expected-squared-error n)
               (/ (variance-of-proportion) n)))
  ...)
```

***

## Connection to Prior ACL2 Convergence Work

The structural proof strategy here parallels the "ceiling proof" and "binomial proof" strategies used in ACL2s to prove \(\lim_{n \to \infty} \alpha^n = 0\) for \(\alpha \in [0,1)\). In that work, the key steps were:[^4]
- Construct an explicit \(\delta\) function (Milestone 6 here: the expected value itself).
- Prove the intermediary bound by induction (Milestones 2 and 3 here).
- Apply the binomial theorem or ceiling lemmas for rational steps (Milestone 1 here: Catalan identities).

For series and integral proofs involving transcendentals, the Fourier series formalization provides the closest precedent: it proves sum rules for infinite series integrals, applies FTC-2 to parameterized functions, and uses the overspill principle for uniform convergence — all of which are needed here (Milestones 4 and 5).[^2]

***

## Conclusion

The coin-toss π estimator rests on a clean chain of mathematical reasoning: Catalan number combinatorics → stopping time distribution → algebraic proportion identity → arcsin series identity → integration by parts. Each link translates naturally into ACL2(r) lemmas. The most technically demanding step is Milestone 4 (the Catalan series integral), which requires the sum rule for infinite series integration from the Fourier series book. All other milestones draw on well-established ACL2(r) infrastructure for arithmetic, transcendentals, and basic analysis. An organized approach — completing milestones in order and using existing community books — gives the coding agent a realistic path to a complete mechanical proof.

---

## References

1. [In Praise of Stupid Questions | - James Propp - WordPress.com](https://mathenchant.wordpress.com/2026/03/12/in-praise-of-stupid-questions/) - So when he heard about my new way of estimating pi, he had the idea of using his 10,000 coin flips t...

2. [[PDF] Fourier Series Formalization in ACL2(r) - CSE CGI Server](https://cgi.cse.unsw.edu.au/~eptcs/paper.cgi?ACL22015.4.pdf) - We formalize some basic properties of Fourier series in the logic of ACL2(r), which is a variant of....

3. [Power Series Expansion for Real Arcsine Function - ProofWiki](https://proofwiki.org/wiki/Power_Series_Expansion_for_Real_Arcsine_Function) - So by the Comparison Test, the Taylor series is convergent for −1≤x≤1. ◼. Also see. Power Series Exp...

4. [[PDF] Real Analysis Using ACL2 - CSE CGI Server](https://cgi.cse.unsw.edu.au/~eptcs/paper.cgi?ACL22023.6.pdf) - Whereas the most obvious proof strategy involves the logarithm, whose codomain includes irrationals,...

5. [[PDF] Non-Standard Analysis in ACL2](https://www.cs.uwyo.edu/~ruben/static/pdf/nsa.pdf) - This will lay the theoretical foundation for the introduction of non-standard analysis into ACL2, pr...

6. [[PDF] Real Vector Spaces and the Cauchy-Schwarz Inequality in ACL2(r)](https://arxiv.org/pdf/1810.04315.pdf) - We present a mechanical proof of the Cauchy-Schwarz inequality in ACL2(r) and a formalisation of the...


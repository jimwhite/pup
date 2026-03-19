;;;; test_lisp2nb.lisp — Tests for lisp2nb convert-file
;;;;
;;;; Run via:
;;;;   sbcl --noinform --non-interactive --disable-debugger \
;;;;        --load context/script2notebook/test_lisp2nb.lisp
;;;;
;;;; Exit code 0 = all pass, 1 = failures.

;;; ─── Bootstrap ──────────────────────────────────────────────────────

(require :asdf)

(let ((here (make-pathname :defaults *load-truename* :name nil :type nil)))
  (pushnew (merge-pathnames #p"../rewrite-cl/" here)
           asdf:*central-registry* :test #'equal))

(asdf:load-system "rewrite-cl" :verbose nil)
(ql:quickload '("yason" "babel") :silent t)

(load (merge-pathnames "lisp2nb.lisp"
                       (make-pathname :defaults *load-truename*
                                      :name nil :type nil)))

(in-package #:lisp2nb)

;;; ─── Test infrastructure ───────────────────────────────────────────

(defvar *test-pass* 0)
(defvar *test-fail* 0)

(defun test-check (name condition)
  (if condition
      (progn (format t "  PASS: ~A~%" name) (incf *test-pass*))
      (progn (format t "  FAIL: ~A~%" name) (incf *test-fail*))))

(defun write-temp-lisp (name content)
  "Write CONTENT to /tmp/NAME.lisp and return the pathname."
  (let ((path (make-pathname :directory '(:absolute "tmp")
                             :name name :type "lisp")))
    (with-open-file (out path :direction :output
                              :if-exists :supersede
                              :external-format :utf-8)
      (write-string content out))
    path))

(defun read-notebook-json (path)
  "Read a .ipynb file and return the parsed JSON hash-table."
  (with-open-file (in path :external-format :utf-8)
    (yason:parse in)))

(defun nb-cells (nb)
  "Return the cells list from notebook hash-table NB."
  (gethash "cells" nb))

(defun cell-source-text (cell)
  "Concatenate the source lines of CELL into a single string."
  (format nil "~{~A~}" (gethash "source" cell)))

(defun cell-type-of (cell)
  (gethash "cell_type" cell))

(defun cells-of-type (type cells)
  "Filter CELLS (a list) to those with cell_type = TYPE."
  (remove-if-not (lambda (c) (string= type (cell-type-of c))) cells))

;;; ─── Tests: basic conversion ───────────────────────────────────────

(format t "~%=== basic conversion ===~%")

;; Simple single-form file
(let* ((src "(defun foo (x) (+ x 1))
")
       (input (write-temp-lisp "test-basic" src))
       (output (make-pathname :defaults input :type "ipynb")))
  (convert-file input :output-path output)
  (let* ((nb (read-notebook-json output))
         (cells (nb-cells nb)))
    (test-check "single form produces cells" (plusp (length cells)))
    (let ((code-cells (remove-if-not
                       (lambda (c) (string= "code" (cell-type-of c)))
                       cells)))
      (test-check "single form has code cell" (= 1 (length code-cells)))
      (test-check "code cell source matches"
                  (string= (string-trim '(#\Newline) src)
                           (string-trim '(#\Newline)
                                        (cell-source-text (first code-cells))))))))

;; Multiple forms
(let* ((src "(defun bar (x) x)

(defun baz (y) y)
")
       (input (write-temp-lisp "test-multi" src))
       (output (make-pathname :defaults input :type "ipynb")))
  (convert-file input :output-path output)
  (let* ((nb (read-notebook-json output))
         (cells (nb-cells nb))
         (code-cells (remove-if-not
                      (lambda (c) (string= "code" (cell-type-of c)))
                      cells)))
    (test-check "two forms produce two code cells" (= 2 (length code-cells)))))

;;; ─── Tests: comments become markdown ───────────────────────────────

(format t "~%=== comment → markdown ===~%")

;; Attached comment (no blank line) stays with the code cell
(let* ((src "; This is attached
(defun foo (x) x)
")
       (input (write-temp-lisp "test-comment" src))
       (output (make-pathname :defaults input :type "ipynb")))
  (convert-file input :output-path output :markdown-bracket :none)
  (let* ((nb (read-notebook-json output))
         (cells (nb-cells nb))
         (code-cells (remove-if-not
                      (lambda (c) (string= "code" (cell-type-of c)))
                      cells)))
    (test-check "attached comment → code cell (not markdown)"
                (= 1 (length code-cells)))
    (test-check "attached comment text in code cell"
                (search "attached"
                        (cell-source-text (first code-cells))))))

;; Detached comment (blank line separates comment from form)
(let* ((src "; Section heading

(defun foo (x) x)
")
       (input (write-temp-lisp "test-detached" src))
       (output (make-pathname :defaults input :type "ipynb")))
  (convert-file input :output-path output :markdown-bracket :none)
  (let* ((nb (read-notebook-json output))
         (cells (nb-cells nb))
         (md-cells (remove-if-not
                    (lambda (c) (string= "markdown" (cell-type-of c)))
                    cells)))
    (test-check "detached comment → markdown" (plusp (length md-cells)))
    (when (plusp (length md-cells))
      (test-check "detached comment text"
                  (search "Section heading"
                          (cell-source-text (first md-cells)))))))

;;; ─── Tests: fenced markdown bracket ────────────────────────────────

(format t "~%=== fenced markdown bracket ===~%")

;; Use a detached comment (blank line before form) so it becomes markdown.
(let* ((src "; Comment line

(defun foo (x) x)
")
       (input (write-temp-lisp "test-fenced" src))
       (output (make-pathname :defaults input :type "ipynb")))
  (convert-file input :output-path output :markdown-bracket :fenced)
  (let* ((nb (read-notebook-json output))
         (cells (nb-cells nb))
         (md-cells (remove-if-not
                    (lambda (c) (string= "markdown" (cell-type-of c)))
                    cells)))
    (test-check "fenced has markdown cell" (plusp (length md-cells)))
    (when (plusp (length md-cells))
      (let ((text (cell-source-text (first md-cells))))
        (test-check "fenced bracket has opening ```"
                    (search "```" text))))))

;;; ─── Tests: empty file ─────────────────────────────────────────────

(format t "~%=== empty file ===~%")

(let* ((input (write-temp-lisp "test-empty" ""))
       (output (make-pathname :defaults input :type "ipynb")))
  (convert-file input :output-path output)
  (let* ((nb (read-notebook-json output))
         (cells (nb-cells nb)))
    (test-check "empty file produces notebook" (hash-table-p nb))
    (test-check "empty file has no cells" (= 0 (length cells)))))

;; Whitespace-only file
(let* ((input (write-temp-lisp "test-wsonly" "   
  
"))
       (output (make-pathname :defaults input :type "ipynb")))
  (convert-file input :output-path output)
  (let* ((nb (read-notebook-json output))
         (cells (nb-cells nb)))
    (test-check "whitespace-only file produces notebook" (hash-table-p nb))
    (test-check "whitespace-only file has no code cells"
                (= 0 (count-if (lambda (c) (string= "code" (cell-type-of c)))
                                cells)))))

;;; ─── Tests: notebook structure ─────────────────────────────────────

(format t "~%=== notebook structure ===~%")

(let* ((src "(defun foo (x) x)
")
       (input (write-temp-lisp "test-structure" src))
       (output (make-pathname :defaults input :type "ipynb")))
  (convert-file input :output-path output)
  (let ((nb (read-notebook-json output)))
    (test-check "nbformat = 4" (= 4 (gethash "nbformat" nb)))
    (test-check "nbformat_minor = 5" (= 5 (gethash "nbformat_minor" nb)))
    (let ((meta (gethash "metadata" nb)))
      (test-check "has metadata" (hash-table-p meta))
      (test-check "has kernelspec"
                  (hash-table-p (gethash "kernelspec" meta)))
      (test-check "has language_info"
                  (hash-table-p (gethash "language_info" meta)))
      (test-check "has source_file"
                  (stringp (gethash "source_file" meta))))))

;; Code cells have outputs and execution_count
(let* ((src "(+ 1 2)
")
       (input (write-temp-lisp "test-code-meta" src))
       (output (make-pathname :defaults input :type "ipynb")))
  (convert-file input :output-path output)
  (let* ((nb (read-notebook-json output))
         (cells (nb-cells nb))
         (code-cell (find-if (lambda (c) (string= "code" (cell-type-of c)))
                             cells)))
    (when code-cell
      (multiple-value-bind (val present-p)
          (gethash "outputs" code-cell)
        (declare (ignore val))
        (test-check "code cell has outputs key" present-p))
      (multiple-value-bind (val present-p)
          (gethash "execution_count" code-cell)
        (declare (ignore val))
        (test-check "code cell has execution_count key" present-p)))))

;;; ─── Tests: provenance metadata ────────────────────────────────────

(format t "~%=== provenance metadata ===~%")

(let* ((src "(defun foo (x) x)
")
       (input (write-temp-lisp "test-provenance" src))
       (output (make-pathname :defaults input :type "ipynb")))
  (convert-file input :output-path output)
  (let* ((nb (read-notebook-json output))
         (cells (nb-cells nb))
         (code-cell (find-if (lambda (c) (string= "code" (cell-type-of c)))
                             cells)))
    (when code-cell
      (let* ((meta (gethash "metadata" code-cell))
             (prov (and meta (gethash "provenance" meta))))
        (test-check "code cell has provenance" (hash-table-p prov))
        (when prov
          (test-check "provenance has start" (integerp (gethash "start" prov)))
          (test-check "provenance has end" (integerp (gethash "end" prov)))
          (test-check "provenance end > start"
                      (> (gethash "end" prov) (gethash "start" prov))))))))

;;; ─── Tests: placeholder on parse error ─────────────────────────────

(format t "~%=== placeholder on parse error ===~%")

;; Write a file with an unmatched open paren to trigger a parse error.
(let* ((bad-src "(defun foo () (+ 1")
       (input (write-temp-lisp "test-parse-error" bad-src))
       (output (make-pathname :defaults input :type "ipynb")))
  (convert-file input :output-path output :markdown-bracket :none)
  (let* ((nb (read-notebook-json output))
         (cells (nb-cells nb)))
    (test-check "parse error produces notebook" (hash-table-p nb))
    (test-check "placeholder has cells" (plusp (length cells)))
    (let ((md-cell (find-if (lambda (c) (string= "markdown" (cell-type-of c)))
                            cells)))
      (test-check "placeholder has markdown cell" md-cell)
      (when md-cell
        (test-check "placeholder mentions parse error"
                    (search "Parse error"
                            (cell-source-text md-cell)))))
    (let ((code-cell (find-if (lambda (c) (string= "code" (cell-type-of c)))
                              cells)))
      (test-check "placeholder has code cell with raw source" code-cell)
      (when code-cell
        (test-check "placeholder code has original source"
                    (search "defun" (cell-source-text code-cell)))))))

;;; ─── Tests: package-locked symbols ─────────────────────────────────

(format t "~%=== package-locked symbols ===~%")

;; ACL2 sources intern into CL package — this must not crash.
(let* ((src "(in-package \"ACL2\")
(defun common-lisp::th (x) x)
")
       (input (write-temp-lisp "test-pkg-lock" src))
       (output (make-pathname :defaults input :type "ipynb")))
  (convert-file input :output-path output)
  (let* ((nb (read-notebook-json output))
         (cells (nb-cells nb)))
    (test-check "package-locked file converts" (plusp (length cells)))))

;;; ─── Tests: portcullis injection ───────────────────────────────────

(format t "~%=== portcullis injection ===~%")

;; Create a .lisp with a companion .acl2 file
(let* ((input (write-temp-lisp "test-portcullis" "(defun foo (x) x)
"))
       (acl2-path (make-pathname :defaults input :type "acl2"))
       (output (make-pathname :defaults input :type "ipynb")))
  ;; Write companion .acl2 file
  (with-open-file (out acl2-path :direction :output
                                  :if-exists :supersede)
    (write-string "(include-book \"std/top\" :dir :system)" out))
  (convert-file input :output-path output :inject-portcullis t)
  (let* ((nb (read-notebook-json output))
         (meta (gethash "metadata" nb))
         (port (and meta (gethash "acl2_portcullis" meta))))
    (test-check "portcullis metadata injected" (hash-table-p port))
    (when port
      (test-check "portcullis has companion content"
                  (plusp (hash-table-count port)))))
  ;; Cleanup .acl2
  (ignore-errors (delete-file acl2-path)))

;;; ─── Tests: real ACL2 files ─────────────────────────────────────────

(format t "~%=== real ACL2 files ===~%")

;; Test a few known files from the ACL2 source tree
(dolist (relpath '("acl2.lisp" "axioms.lisp" "basis-a.lisp"))
  (let ((source (merge-pathnames relpath #p"/home/acl2/")))
    (if (probe-file source)
        (let ((output (make-pathname :directory '(:absolute "tmp")
                                     :name (format nil "test-real-~A"
                                                   (pathname-name source))
                                     :type "ipynb")))
          (handler-case
              (progn
                (convert-file source :output-path output
                                     :markdown-bracket :fenced)
                (let* ((nb (read-notebook-json output))
                       (cells (nb-cells nb))
                       (code-cells (remove-if-not
                                    (lambda (c)
                                      (string= "code" (cell-type-of c)))
                                    cells)))
                  (test-check (format nil "~A converts" relpath)
                              (plusp (length cells)))
                  (test-check (format nil "~A has code cells" relpath)
                              (plusp (length code-cells))))
                ;; Cleanup
                (ignore-errors (delete-file output)))
            (error (e)
              (format t "  ERROR: ~A~%" e)
              (test-check (format nil "~A converts without error" relpath) nil))))
        (format t "  --- ~A not found, skipping ---~%" relpath))))

;;; ─── Tests: Aleo fancy-string files (known parse issue) ────────────

(format t "~%=== Aleo fancy-string placeholder ===~%")

;; These files contain #{"""..."""} which rewrite-cl cannot yet parse.
;; They should produce placeholder notebooks, not crash.
(let ((aleo-dir #p"/home/acl2/books/projects/aleo/vm/circuits/samples/"))
  (if (probe-file aleo-dir)
      (let ((samples (directory (merge-pathnames
                                (make-pathname :name :wild :type "lisp")
                                aleo-dir))))
        (if samples
            (let ((sample (first samples)))
              (let ((output (make-pathname :directory '(:absolute "tmp")
                                           :name "test-aleo-placeholder"
                                           :type "ipynb")))
                (convert-file sample :output-path output
                                     :markdown-bracket :fenced)
                (let* ((nb (read-notebook-json output))
                       (cells (nb-cells nb)))
                  (test-check "Aleo file produces notebook (placeholder or full)"
                              (hash-table-p nb))
                  (test-check "Aleo file has cells"
                              (plusp (length cells))))
                (ignore-errors (delete-file output))))
            (format t "  --- no Aleo sample .lisp files found, skipping ---~%")))
      (format t "  --- Aleo samples dir not found, skipping ---~%")))

;;; ─── Tests: convert-file return value ──────────────────────────────

(format t "~%=== return value ===~%")

(let* ((input (write-temp-lisp "test-retval" "(+ 1 2)
"))
       (output (make-pathname :defaults input :type "ipynb"))
       (result (convert-file input :output-path output)))
  (test-check "convert-file returns output path"
              (equal output result)))

;;; ─── Tests: feature conditionals (#+feat / #-feat) ─────────────────

(format t "~%=== feature conditionals ===~%")

;; Feature conditional on same line as form — should be one code cell.
(let* ((src "#+sbcl (require :sb-posix)
")
       (input (write-temp-lisp "test-feat-same-line" src))
       (output (make-pathname :defaults input :type "ipynb")))
  (convert-file input :output-path output)
  (let* ((nb (read-notebook-json output))
         (cells (nb-cells nb))
         (code-cells (cells-of-type "code" cells)))
    (test-check "feat same line → 1 code cell" (= 1 (length code-cells)))
    (when code-cells
      (test-check "feat same line text has #+sbcl"
                  (search "#+sbcl" (cell-source-text (first code-cells))))
      (test-check "feat same line text has require"
                  (search "require" (cell-source-text (first code-cells)))))))

;; Feature conditional on separate line from form — must merge.
(let* ((src "#+sbcl
(require :sb-posix)
")
       (input (write-temp-lisp "test-feat-next-line" src))
       (output (make-pathname :defaults input :type "ipynb")))
  (convert-file input :output-path output)
  (let* ((nb (read-notebook-json output))
         (cells (nb-cells nb))
         (code-cells (cells-of-type "code" cells)))
    (test-check "feat next line → 1 code cell" (= 1 (length code-cells)))
    (when code-cells
      (let ((text (cell-source-text (first code-cells))))
        (test-check "feat next line has #+sbcl" (search "#+sbcl" text))
        (test-check "feat next line has require" (search "require" text))))))

;; Negative feature conditional on separate line.
(let* ((src "#-ccl
(require :sb-posix)
")
       (input (write-temp-lisp "test-feat-negative" src))
       (output (make-pathname :defaults input :type "ipynb")))
  (convert-file input :output-path output)
  (let* ((nb (read-notebook-json output))
         (cells (nb-cells nb))
         (code-cells (cells-of-type "code" cells)))
    (test-check "feat negative → 1 code cell" (= 1 (length code-cells)))
    (when code-cells
      (test-check "feat negative has #-ccl"
                  (search "#-ccl" (cell-source-text (first code-cells)))))))

;; Chained feature conditionals: #+sbcl #+unix on separate lines.
(let* ((src "#+sbcl
#+unix
(require :sb-posix)
")
       (input (write-temp-lisp "test-feat-chained" src))
       (output (make-pathname :defaults input :type "ipynb")))
  (convert-file input :output-path output)
  (let* ((nb (read-notebook-json output))
         (cells (nb-cells nb))
         (code-cells (cells-of-type "code" cells)))
    (test-check "feat chained → 1 code cell" (= 1 (length code-cells)))
    (when code-cells
      (let ((text (cell-source-text (first code-cells))))
        (test-check "feat chained has #+sbcl" (search "#+sbcl" text))
        (test-check "feat chained has #+unix" (search "#+unix" text))
        (test-check "feat chained has require" (search "require" text))))))

;; Feature conditional with block comment between — allowed by ANSI CL.
(let* ((src "#+sbcl #|platform guard|#
(require :sb-posix)
")
       (input (write-temp-lisp "test-feat-block-comment" src))
       (output (make-pathname :defaults input :type "ipynb")))
  (convert-file input :output-path output)
  (let* ((nb (read-notebook-json output))
         (cells (nb-cells nb))
         (code-cells (cells-of-type "code" cells)))
    (test-check "feat + block comment → 1 code cell"
                (= 1 (length code-cells)))
    (when code-cells
      (test-check "feat + block comment has require"
                  (search "require"
                          (cell-source-text (first code-cells)))))))

;; Feature conditional with line comment between — must not split.
(let* ((src "#+sbcl
; platform-specific require
(require :sb-posix)
")
       (input (write-temp-lisp "test-feat-line-comment" src))
       (output (make-pathname :defaults input :type "ipynb")))
  (convert-file input :output-path output)
  (let* ((nb (read-notebook-json output))
         (cells (nb-cells nb))
         (code-cells (cells-of-type "code" cells)))
    (test-check "feat + line comment → 1 code cell"
                (= 1 (length code-cells)))
    (when code-cells
      (test-check "feat + line comment has require"
                  (search "require"
                          (cell-source-text (first code-cells)))))))

;; Feature conditional with or-expression: #-(or ccl sbcl)
(let* ((src "#-(or ccl sbcl)
(error \"unsupported platform\")
")
       (input (write-temp-lisp "test-feat-or-expr" src))
       (output (make-pathname :defaults input :type "ipynb")))
  (convert-file input :output-path output)
  (let* ((nb (read-notebook-json output))
         (cells (nb-cells nb))
         (code-cells (cells-of-type "code" cells)))
    (test-check "feat or-expr → 1 code cell" (= 1 (length code-cells)))
    (when code-cells
      (let ((text (cell-source-text (first code-cells))))
        (test-check "feat or-expr has #-(or" (search "#-(or" text))
        (test-check "feat or-expr has error" (search "error" text))))))

;; Multiple feature-conditioned forms should each get their own cell.
(let* ((src "#+sbcl
(require :sb-posix)

#+ccl
(require :ccl)
")
       (input (write-temp-lisp "test-feat-multi" src))
       (output (make-pathname :defaults input :type "ipynb")))
  (convert-file input :output-path output)
  (let* ((nb (read-notebook-json output))
         (cells (nb-cells nb))
         (code-cells (cells-of-type "code" cells)))
    (test-check "multiple feat forms → 2 code cells"
                (= 2 (length code-cells)))
    (when (= 2 (length code-cells))
      (test-check "first feat cell has sb-posix"
                  (search "sb-posix"
                          (cell-source-text (first code-cells))))
      (test-check "second feat cell has ccl"
                  (search "ccl"
                          (cell-source-text (second code-cells)))))))

;; Feature conditional at EOF (no following form) — emit what we have.
(let* ((src "(defun foo () t)
#+sbcl
")
       (input (write-temp-lisp "test-feat-eof" src))
       (output (make-pathname :defaults input :type "ipynb")))
  (convert-file input :output-path output)
  (let* ((nb (read-notebook-json output))
         (cells (nb-cells nb))
         (code-cells (cells-of-type "code" cells)))
    (test-check "feat at EOF produces notebook" (hash-table-p nb))
    (test-check "feat at EOF has code cell for defun"
                (>= (length code-cells) 1))))

;; Real file: centaur/bridge/top.lisp (has #+sbcl on separate lines)
(let ((source #p"/home/acl2/books/centaur/bridge/top.lisp"))
  (if (probe-file source)
      (let ((output (make-pathname :directory '(:absolute "tmp")
                                   :name "test-bridge-top"
                                   :type "ipynb")))
        (handler-case
            (progn
              (convert-file source :output-path output
                                   :markdown-bracket :fenced)
              (let* ((nb (read-notebook-json output))
                     (cells (nb-cells nb))
                     (code-cells (cells-of-type "code" cells)))
                (test-check "bridge/top.lisp converts"
                            (plusp (length cells)))
                ;; Find a cell containing #+sbcl and include-book hunchentoot
                (let ((merged-cell
                        (find-if (lambda (c)
                                   (let ((text (cell-source-text c)))
                                     (and (search "#+sbcl" text)
                                          (search "hunchentoot" text))))
                                 code-cells)))
                  (test-check "bridge/top.lisp: #+sbcl + hunchentoot in one cell"
                              merged-cell)))
              (ignore-errors (delete-file output)))
          (error (e)
            (format t "  ERROR: ~A~%" e)
            (test-check "bridge/top.lisp converts without error" nil))))
      (format t "  --- bridge/top.lisp not found, skipping ---~%")))

;;; ─── Cleanup temp files ────────────────────────────────────────────

(dolist (name '("test-basic" "test-multi" "test-comment" "test-detached"
               "test-fenced" "test-empty" "test-wsonly" "test-structure"
               "test-code-meta" "test-provenance" "test-parse-error"
               "test-pkg-lock" "test-portcullis" "test-retval"
               "test-feat-same-line" "test-feat-next-line"
               "test-feat-negative" "test-feat-chained"
               "test-feat-block-comment" "test-feat-line-comment"
               "test-feat-or-expr"
               "test-feat-multi" "test-feat-eof"))
  (ignore-errors (delete-file (format nil "/tmp/~A.lisp" name)))
  (ignore-errors (delete-file (format nil "/tmp/~A.ipynb" name))))

;;; ─── Summary ───────────────────────────────────────────────────────

(format t "~%========================================~%")
(format t "Results: ~D passed, ~D failed~%" *test-pass* *test-fail*)
(format t "========================================~%")

(uiop:quit (if (> *test-fail* 0) 1 0))

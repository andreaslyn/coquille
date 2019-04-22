
" Vim syntax file
" Language: Coq
" Maintainer: Andreas Lynge <andreaslyn@gmail.com>

if version < 600
  syntax clear
elseif exists("b:current_syntax")
  finish
endif

syn keyword coqKeyword let fix cofix for if IF then else λ fun forall exists match mod end exists2 struct where with using at as return in
hi def link coqKeyword Keyword

syn keyword coqSpecialName _ Set Type Prop SProp
hi def link coqSpecialName Function

syn keyword coqProof Proof Qed Defined
hi def link coqProof Underlined

syn keyword coqAdmitted Admitted
hi def link coqAdmitted Error

syn match coqOperator /[!%^&*\-=+~<>/?:#|\\$@]\+/
hi def link coqOperator NONE

syn match coqDotDot /\.\.\+/
hi def link coqDotDot coqOperator

syn match coqKeyOperator /\%(|\|->\|:=\|=>\|:\|∀\|∃\|→\)[!%^&*\-=+~<>/?:#|\\$@]\@!/
hi def link coqKeyOperator Keyword

syn region coqString start="\"" end="\""
hi def link coqString String

syn keyword coqTodo TODO FIXME XXX NOTE contained
hi def link coqTodo Todo

syn match coqPunctation /[;,(){}[\]]/
hi def link coqPunctation Special

syn match coqDot /\.\_s/
hi def link coqDot coqPunctation

syn region coqBlockComment start="(\*" end="\*)" contains=coqBlockComment,coqTodo
hi def link coqBlockComment Comment

syn sync minlines=500
syn sync maxlines=700
"syn sync linebreaks=3
let b:current_syntax = "coq"

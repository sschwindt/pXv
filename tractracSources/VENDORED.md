# Vendored reference: TracTrac

This directory holds an unmodified copy of TracTrac (Heyman 2019), kept as a
**read-only reference**. pXv never imports from it at runtime.

Two reasons for the separation:

1. `matplotlibrc` here forces the `TkAgg` backend, which would fight the PySide6
   event loop if it were ever picked up by an import.
2. The code targets an older Python and carries module-level plotting state.

pXv reimplements the published algorithm in `pxv/velocimetry/ptv.py` against the
same parameter names, so existing `*_par.txt` settings carry over. Both projects
are MIT licensed; `LICENSE` in this directory preserves the upstream notice.

Reference:

> Heyman, J., 2019. TracTrac: A fast multi-object tracking algorithm for motion
> estimation. *Computers & Geosciences* 128, 11-18.
> https://doi.org/10.1016/j.cageo.2019.03.007

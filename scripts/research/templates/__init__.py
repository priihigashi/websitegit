"""SH-104 brand-locked carousel templates.

Templates are versioned reference HTMLs in `<niche>/_reference/` plus
a Python module per niche that exposes:
  - SHARED_CSS  : <style> block used by every slide
  - SVG_FILTERS : <svg> defs for duotone filters
  - SLIDE_COVER, SLIDE_BIOGRAPHY, SLIDE_EVIDENCE, SLIDE_SOURCES :
      Python format strings; substitute via .format(**kwargs)
  - build_carousel_html(spec) -> str : compose full HTML

Why module-as-template: keeps templates versioned in repo (audit trail
+ diffable), avoids runtime Drive fetches, decouples render from
network I/O. The HTML in `_reference/` is the brand-locked source — if
you change the module, also update the reference file (or vice-versa)
so the registry stays trustworthy.
"""

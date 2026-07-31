"""Clousight / 云计算指北 brand tokens for the default report theme.

Colors + fonts from the cloudNorth design system; the logo is the official
cloud-and-north-arrow mark, inlined so the report stays self-contained. The
gradient id is namespaced so it can never collide with a chart's SVG.
"""
from __future__ import annotations

BRAND = {"deep_blue": "#1E3A8A", "blue": "#3B82F6", "green": "#10B981",
         "amber": "#F59E0B", "red": "#EF4444", "blue_50": "#EFF6FF"}
FONT_DISPLAY = '"Space Grotesk", system-ui, sans-serif'
FONT_BODY = '"DM Sans", -apple-system, "Segoe UI", Roboto, sans-serif'
BRAND_NAME_ZH = "云计算指北 · 指北测评"
BRAND_NAME_EN = "Clousight Bench"

LOGO_SVG = (
    "<svg width='40' height='40' viewBox='0 0 128 128' "
    "xmlns='http://www.w3.org/2000/svg'>"
    "<defs><linearGradient id='clousightCloudGradient' x1='0%' y1='0%' x2='100%' y2='100%'>"
    "<stop offset='0%' stop-color='#1E3A8A'/><stop offset='100%' stop-color='#3B82F6'/>"
    "</linearGradient></defs>"
    "<path d='M98,60 C98,45.088 85.912,33 71,33 C59.062,33 48.892,40.332 44.718,50.743 "
    "C42.584,49.642 40.126,49 37.5,49 C29.492,49 23,55.492 23,63.5 C23,64.642 23.141,65.748 "
    "23.398,66.806 C18.109,69.362 14.5,74.792 14.5,81 C14.5,90.665 22.335,98.5 32,98.5 "
    "L96.5,98.5 C107.27,98.5 116,89.77 116,79 C116,69.634 108.333,61.781 98,60 Z' "
    "fill='url(#clousightCloudGradient)'/>"
    "<path d='M64,24 L74,44 L64,39 L54,44 Z' fill='#10B981'/>"
    "</svg>"
)

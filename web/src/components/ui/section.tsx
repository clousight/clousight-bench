// The hairline surface. Replaces the vendored shadcn card.
//
// A card draws a box; a section separates with a rule and whitespace. The box
// is what made every screen read as a generic admin panel, and on screens that
// are already dense it also cost vertical space to say nothing. The API is
// deliberately 1:1 with the card's so migrating a view is a rename.
import { forwardRef, type HTMLAttributes } from "react";

import { cn } from "@/lib/utils";

export const Section = forwardRef<HTMLElement, HTMLAttributes<HTMLElement>>(
  ({ className, ...props }, ref) => (
    <section ref={ref} className={cn("border-t border-border", className)} {...props} />
  ),
);
Section.displayName = "Section";

export const SectionHead = forwardRef<HTMLDivElement, HTMLAttributes<HTMLDivElement>>(
  ({ className, ...props }, ref) => (
    <div ref={ref} className={cn("flex flex-col gap-0.5 pt-3 pb-2", className)} {...props} />
  ),
);
SectionHead.displayName = "SectionHead";

// A micro-label, not a heading: mono, uppercase, tracked out, recessive. It
// names the section without competing with the numbers inside it.
export const SectionTitle = forwardRef<HTMLHeadingElement, HTMLAttributes<HTMLHeadingElement>>(
  ({ className, ...props }, ref) => (
    <h3
      ref={ref}
      className={cn(
        "font-mono text-[10px] font-normal uppercase leading-none tracking-[0.1em] text-muted-foreground",
        className,
      )}
      {...props}
    />
  ),
);
SectionTitle.displayName = "SectionTitle";

export const SectionNote = forwardRef<HTMLParagraphElement, HTMLAttributes<HTMLParagraphElement>>(
  ({ className, ...props }, ref) => (
    <p ref={ref} className={cn("text-xs text-muted-foreground", className)} {...props} />
  ),
);
SectionNote.displayName = "SectionNote";

export const SectionBody = forwardRef<HTMLDivElement, HTMLAttributes<HTMLDivElement>>(
  ({ className, ...props }, ref) => (
    <div ref={ref} className={cn("pb-5", className)} {...props} />
  ),
);
SectionBody.displayName = "SectionBody";

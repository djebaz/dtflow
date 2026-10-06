import * as React from "react";

/**
 * Titled group card: surface panel with a header row (optional icon + heading) and a contents region.
 */
export interface SectionCardProps {
  /** Heading text (panelHeading: 9pt, semibold). */
  title: string;
  /** Optional 16px leading glyph; supply a real icon asset. */
  icon?: React.ReactNode;
  /** Header shows an operable checkbox; unchecked hides the contents. */
  checkable?: boolean;
  checked?: boolean;
  onCheckedChange?: (checked: boolean) => void;
  /** Muted border/heading; contents inert. */
  disabled?: boolean;
  children?: React.ReactNode;
  style?: React.CSSProperties;
}
export declare function SectionCard(props: SectionCardProps): JSX.Element;

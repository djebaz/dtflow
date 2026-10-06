import * as React from "react";

export interface LabelProps {
  /** Text textRole: eyebrow (8pt caps blue), caption (9pt medium secondary), value (semibold), section (11pt semibold), title (17pt), supporting (9pt secondary), identifier (mono 9pt), hint, body. */
  textRole?: "body" | "eyebrow" | "caption" | "value" | "section" | "title" | "supporting" | "identifier" | "hint";
  children?: React.ReactNode;
  className?: string;
  [key: string]: any;
}
export declare function Label(props: LabelProps): JSX.Element;

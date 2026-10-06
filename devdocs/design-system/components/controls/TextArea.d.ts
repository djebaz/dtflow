import * as React from "react";

export interface TextAreaProps {
  invalid?: boolean;
  rows?: number;
  placeholder?: string;
  disabled?: boolean;
  className?: string;
  [key: string]: any;
}
export declare function TextArea(props: TextAreaProps): JSX.Element;

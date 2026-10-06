import * as React from "react";

export interface ComboBoxProps {
  options?: string[];
  invalid?: boolean;
  disabled?: boolean;
  value?: string;
  /** Called with a change-like event: e.target.value is the picked option. */
  onChange?: (e: { target: { value: string } }) => void;
  defaultValue?: string;
  className?: string;
  [key: string]: any;
}
export declare function ComboBox(props: ComboBoxProps): JSX.Element;

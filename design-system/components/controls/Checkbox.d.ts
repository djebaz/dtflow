import * as React from "react";

export interface CheckboxProps {
  label?: React.ReactNode;
  checked?: boolean;
  disabled?: boolean;
  onChange?: React.ChangeEventHandler<HTMLInputElement>;
  [key: string]: any;
}
export declare function Checkbox(props: CheckboxProps): JSX.Element;

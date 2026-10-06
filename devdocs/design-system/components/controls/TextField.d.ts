import * as React from "react";

export interface TextFieldProps {
  invalid?: boolean;
  placeholder?: string;
  disabled?: boolean;
  value?: string;
  onChange?: React.ChangeEventHandler<HTMLInputElement>;
  className?: string;
  [key: string]: any;
}
export declare function TextField(props: TextFieldProps): JSX.Element;

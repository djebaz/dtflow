import * as React from "react";

export interface ThumbnailProps {
  src?: string;
  label?: string;
  selected?: boolean;
  onClick?: React.MouseEventHandler<HTMLButtonElement>;
}
export declare function Thumbnail(props: ThumbnailProps): JSX.Element;

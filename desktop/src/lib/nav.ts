/**
 * View navigation context — lets any view switch the active view without
 * prop-drilling through the registry. Used by "quick action" buttons on
 * Today, Next Rep, etc. that jump to Manual Entry or Agent Chat.
 *
 * No useEffect — state is derived from the App's useState.
 */
import { createContext, useContext } from "react";

export const NavContext = createContext<(viewId: string) => void>(() => {});

/** Navigate to another view by ID. */
export const useNav = () => useContext(NavContext);

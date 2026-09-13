from collections import Counter


class LogOptimizer:
    """
    Compresses noisy, repetitive log output so an LLM (or a human) doesn't
    have to read the same line a thousand times in a row.
    """

    @staticmethod
    def collapse_duplicates(lines: list[str]) -> list[str]:
        """
        Collapse runs of duplicate lines into a single occurrence plus a
        "... repeated N more times ..." marker.

        Note: counts are global (how many times the exact line appears
        anywhere in `lines`), not just within a contiguous run. This matches
        the common case of noisy heartbeats/retries scattered through a file.
        """

        counter = Counter(lines)

        result = []

        seen = set()

        for line in lines:
            if line in seen:
                continue

            seen.add(line)

            result.append(line)

            if counter[line] > 1:
                result.append(f"... repeated {counter[line]-1} more times ...")

        return result

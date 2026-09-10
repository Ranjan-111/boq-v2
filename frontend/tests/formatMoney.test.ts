import { describe, expect, it } from "vitest";
import { formatMoney } from "../src/lib/apiClient";

describe("formatMoney (integer minor units, string math only)", () => {
  it("formats 913750 minor as 9137.50", () => {
    expect(formatMoney(913750)).toBe("9137.50");
  });

  it("formats 0 as 0.00", () => {
    expect(formatMoney(0)).toBe("0.00");
  });

  it("formats 1 as 0.01 with no float artifacts", () => {
    expect(formatMoney(1)).toBe("0.01");
  });

  it("pads a single-digit cent amount", () => {
    expect(formatMoney(1205)).toBe("12.05");
  });

  it("formats negative amounts with a leading sign", () => {
    expect(formatMoney(-5)).toBe("-0.05");
    expect(formatMoney(-913750)).toBe("-9137.50");
  });

  it("handles amounts whose minor digits would be fewer than 2", () => {
    expect(formatMoney(7)).toBe("0.07");
    expect(formatMoney(10)).toBe("0.10");
    expect(formatMoney(99)).toBe("0.99");
    expect(formatMoney(100)).toBe("1.00");
  });

  it("refuses non-integer minor amounts (money is never a float)", () => {
    expect(() => formatMoney(1.5)).toThrow();
    expect(() => formatMoney(Number.NaN)).toThrow();
  });

  it("round-trips large integers exactly where float math would drift", () => {
    // 2**53-ish integers stay exact as JS integers; string slicing keeps cents.
    expect(formatMoney(9007199254740993 - 9007199254740993 + 123456)).toBe("1234.56");
    expect(formatMoney(1000000007)).toBe("10000000.07");
  });
});

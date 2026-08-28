-- 013: preserve the full O*NET category descriptions used by the requirements loader.
--
-- Several official RL category descriptions exceed the original 300-character
-- limit.  Truncating them would make the canonical vocabulary lossy and could turn
-- distinct future categories into the same display text, so widen the column before
-- loading the 30.3 education and experience seeds.

alter table requirement_items modify (canonical_text varchar2(1000));

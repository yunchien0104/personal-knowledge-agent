from personal_brain.topic_backfill import classify


TOPIC_ID = "11000000-0000-4000-8000-000000000001"


class FakeClient:
    def __init__(self, output):
        self.output = output

    def generate(self, system_prompt, user_input, output_schema):
        assert "Never invent" in system_prompt
        assert user_input["availableTopics"]
        assert output_schema["required"] == ["topicIds"]
        return self.output


def candidate():
    return {
        "title": "AI product update",
        "body": "A new software feature.",
        "availableTopics": [{
            "id": TOPIC_ID, "name": "科技與產品", "description": "軟硬體與數位趨勢",
        }],
    }


def test_backfill_accepts_only_existing_topic_ids():
    assert classify(FakeClient({"topicIds": [TOPIC_ID]}), candidate()) == [TOPIC_ID]


def test_backfill_rejects_invented_topic_ids():
    try:
        classify(FakeClient({"topicIds": ["22000000-0000-4000-8000-000000000002"]}), candidate())
    except ValueError as error:
        assert str(error) == "invalid_topic_ids"
    else:
        raise AssertionError("invented topic id must fail")


def test_backfill_keeps_note_unclassified_when_no_topics_exist():
    row = candidate()
    row["availableTopics"] = []
    assert classify(FakeClient({"topicIds": [TOPIC_ID]}), row) == []

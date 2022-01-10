# meanwhile

Run jobs while you're away from the computer.

## Usage

```bash
python meanwhile --job example_job.json --delay 10 --workdir workdir
```

example_job.json
```json
{
  "jobs": [
    {
      "name": "test1",
      "command": "echo \"a\";sleep 5; echo \"b\""
    },
    {
      "name": "test2",
      "command": "echo \"c\";sleep 5; echo \"d\""
    }
  ]
}
```

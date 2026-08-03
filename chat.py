from openai import OpenAI
import sys

client = OpenAI(
    base_url="http://localhost:8081/v1",
    api_key="dummy"
)

print("=========================================")
print("🤖 Gemini Interactive Chat")
print("Type 'exit' or 'quit' to end the session.")
print("=========================================\n")

chat_history = []

while True:
    try:
        user_input = input("You: ")
        if user_input.lower() in ['exit', 'quit']:
            print("Goodbye!")
            break
            
        if not user_input.strip():
            continue

        chat_history.append({"role": "user", "content": user_input})

        # Call the local API
        response = client.chat.completions.create(
            model="gemini-3.5-flash-thinking",
            messages=chat_history,
            stream=True
        )

        print("Gemini: ", end="", flush=True)
        
        full_response = ""
        for chunk in response:
            if chunk.choices[0].delta.content:
                content = chunk.choices[0].delta.content
                print(content, end="", flush=True)
                full_response += content
                
        print("\n")
        chat_history.append({"role": "assistant", "content": full_response})

    except KeyboardInterrupt:
        print("\nGoodbye!")
        sys.exit(0)
    except Exception as e:
        print(f"\nError: {e}\n")
